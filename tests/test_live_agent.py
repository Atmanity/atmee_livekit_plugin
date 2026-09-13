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

Note: the autouse ``_env`` fixture in conftest deliberately does NOT stub the
environment for ``live``-marked tests, so this reads the real credentials.
"""

from __future__ import annotations

import array
import asyncio
import os
import time

import pytest

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
        video_seen = asyncio.Event()
        # A published audio track emits frames continuously, including silence,
        # so a raw frame count proves nothing. Count only "voiced" frames —
        # peak amplitude above a small floor — and require them to appear after
        # the agent replies. VOICED_FLOOR is well above line noise, well below
        # speech (int16 full scale is 32767).
        voiced_frames = 0
        audio_tasks: set[asyncio.Task[None]] = set()
        VOICED_FLOOR = 500

        async def _drain_audio(track: rtc.Track) -> None:
            nonlocal voiced_frames
            stream = rtc.AudioStream(track)
            try:
                async for event in stream:
                    samples = array.array("h", bytes(event.frame.data))
                    if samples and max(abs(s) for s in samples) > VOICED_FLOOR:
                        voiced_frames += 1
            finally:
                await stream.aclose()

        # Fire on the avatar's tracks as they are subscribed — this is what
        # makes the audio counter robust: it starts only once the track
        # exists, unlike polling the participant before it has joined.
        @room.on("track_subscribed")
        def _on_track(
            track: rtc.Track, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant
        ) -> None:
            if p.identity != "atmee-avatar-agent":
                return
            if track.kind == rtc.TrackKind.KIND_VIDEO:
                video_seen.set()
            elif track.kind == rtc.TrackKind.KIND_AUDIO:
                t = asyncio.create_task(_drain_audio(track))
                audio_tasks.add(t)
                t.add_done_callback(audio_tasks.discard)

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

            await asyncio.wait_for(video_seen.wait(), timeout=150)
            print(f"avatar video after {time.time() - t0:.1f}s (session {avatar.session_id})")

            # Only speech produced from here on should count, so start from the
            # voiced frames seen so far (idle rendering should be silence, but
            # this makes the check independent of that).
            voiced_before = voiced_frames
            await session.generate_reply(instructions="Greet the user warmly.")
            # Wait for the reply's speech to reach the avatar's audio track.
            for _ in range(60):
                if voiced_frames > voiced_before:
                    break
                await asyncio.sleep(0.5)
            assert voiced_frames > voiced_before, (
                "no voiced avatar audio after the agent replied "
                f"(voiced_before={voiced_before}, voiced_after={voiced_frames})"
            )
            print(f"voiced avatar audio frames from the reply: {voiced_frames - voiced_before}")
        finally:
            for t in list(audio_tasks):
                await utils.aio.cancel_and_wait(t)
            await avatar.aclose()
            await session.aclose()
            await room.disconnect()

        # Atmee settled the session (aclose() called /end).
        status = await avatar.api.get_avatar_session(avatar.session_id)
        assert status.get("status") in ("completed", "failed"), status
        print(f"final Atmee status: {status.get('status')}")
