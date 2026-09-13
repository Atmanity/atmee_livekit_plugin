"""Live end-to-end test: a real GPT voice agent with an Atmee avatar.

This is a manual/live smoke test, not part of the normal suite. It connects
to a real LiveKit room as the agent, runs a real ``AgentSession`` (OpenAI
GPT for the LLM and TTS, silero VAD), renders an Atmee avatar on top, makes
the agent speak, and checks that the avatar joins the room, publishes a video
track, and that its (agent-driven) audio actually flows. Then it closes the
session and confirms Atmee settled it.

It is skipped unless ``RUN_LIVE_ATMEE_AGENT=1`` and every required credential
is present, so CI and ``uv run pytest`` stay offline and fast. Run it by hand::

    RUN_LIVE_ATMEE_AGENT=1 \
    ATMEE_API_KEY=sk_atmee_... ATMEE_AVATAR_ID=... \
    LIVEKIT_URL=wss://... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=... \
    OPENAI_API_KEY=... \
    uv run --group dev --with "livekit-agents[openai,silero]" \
        pytest tests/test_live_agent.py -m live -s

``AVATAR_TEST_LLM_MODEL`` overrides the model (default ``gpt-4o-mini``).
"""

from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.live

REQUIRED_ENV = (
    "ATMEE_API_KEY",
    "ATMEE_AVATAR_ID",
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "OPENAI_API_KEY",
)


def _missing() -> list[str]:
    return [name for name in REQUIRED_ENV if not os.getenv(name)]


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_ATMEE_AGENT") != "1",
        reason="live test; set RUN_LIVE_ATMEE_AGENT=1 to run",
    ),
    pytest.mark.skipif(bool(_missing()), reason=f"missing env: {', '.join(_missing())}"),
]


async def test_gpt_agent_drives_the_avatar() -> None:
    from livekit import api, rtc
    from livekit.agents import Agent, AgentSession, utils

    try:
        from livekit.plugins import openai, silero
    except ModuleNotFoundError as e:  # pragma: no cover - env-dependent
        pytest.skip(f"live agent deps not installed: {e}")

    from livekit.plugins import atmee

    model = os.environ.get("AVATAR_TEST_LLM_MODEL", "gpt-4o-mini")
    room_name = f"atmee-live-{int(time.time())}"
    token = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity("live-test-agent")
        .with_name("live-test-agent")
        .with_kind("agent")
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
        .to_jwt()
    )

    async with utils.http_context.open():
        room = rtc.Room()
        video_seen = utils.aio.Event()  # avatar published video
        audio_frames = 0

        @room.on("track_subscribed")
        def _on_track(
            track: rtc.Track, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant
        ) -> None:
            if p.identity != "atmee-avatar-agent":
                return
            if track.kind == rtc.TrackKind.KIND_VIDEO:
                video_seen.set()

        await room.connect(os.environ["LIVEKIT_URL"], token, rtc.RoomOptions(auto_subscribe=True))

        session = AgentSession(
            stt=openai.STT(),
            llm=openai.LLM(model=model),
            tts=openai.TTS(voice="alloy"),
            vad=silero.VAD.load(),
        )
        avatar = atmee.AvatarSession(
            avatar_id=os.environ["ATMEE_AVATAR_ID"], max_duration_seconds=600
        )
        try:
            t0 = time.time()
            await avatar.start(session, room=room)
            assert avatar.session_id, "no Atmee session was created"

            await session.start(
                agent=Agent(
                    instructions="You are a friendly test avatar. Answer in one short sentence."
                ),
                room=room,
            )

            # Subscribe to the avatar's audio track so we can count frames the
            # agent's speech produced, and make the agent speak.
            async def _count_audio() -> None:
                nonlocal audio_frames
                async for _ in _avatar_audio_frames(room):
                    audio_frames += 1

            audio_task = utils.aio.create_task(_count_audio())
            try:
                await utils.aio.wait_for(video_seen.wait(), timeout=150)
                assert video_seen.is_set(), "avatar never published a video track"
                print(f"avatar video after {time.time() - t0:.1f}s (session {avatar.session_id})")

                await session.generate_reply(instructions="Greet the user warmly.")
                # Give the rendered audio a few seconds to flow through.
                for _ in range(20):
                    if audio_frames > 0:
                        break
                    await _sleep(0.5)
                assert audio_frames > 0, "no avatar audio frames after the agent replied"
                print(f"avatar audio frames observed: {audio_frames}")
            finally:
                await utils.aio.cancel_and_wait(audio_task)
        finally:
            await avatar.aclose()
            await session.aclose()
            await room.disconnect()

        # Atmee settled the session (aclose() called /end).
        status = await avatar.api.get_avatar_session(avatar.session_id)
        assert status.get("status") in ("completed", "failed"), status
        print(f"final Atmee status: {status.get('status')}")


async def _avatar_audio_frames(room):  # type: ignore[no-untyped-def]
    from livekit import rtc

    for participant in room.remote_participants.values():
        if participant.identity != "atmee-avatar-agent":
            continue
        for pub in participant.track_publications.values():
            track = pub.track
            if track is not None and track.kind == rtc.TrackKind.KIND_AUDIO:
                stream = rtc.AudioStream(track)
                async for _event in stream:
                    yield _event
                return
    # No audio track yet; yield nothing (the caller polls audio_frames).
    return
    yield  # pragma: no cover - makes this an async generator


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
