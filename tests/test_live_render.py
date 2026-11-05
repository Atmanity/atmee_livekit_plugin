"""Live end-to-end test: an Atmee avatar rendered into a real LiveKit room, v1 or v2.

A manual/live check, not part of the normal suite, and with no model behind it:
it connects to a real LiveKit room as the agent, starts an ``AvatarSession``
for ``ATMEE_AVATAR_ID`` (any avatar, v1 or v2: the avatar decides), plays
a few seconds of a voice-like signal through the plugin's own audio output,
and checks that the avatar joins, publishes video and audio, reports the
playback, and that Atmee settles the session once it is closed. A v2 avatar
can take minutes to start after a quiet period.

It is skipped unless ``RUN_LIVE_ATMEE_RENDER=1`` and every credential is
present. Run it by hand::

    RUN_LIVE_ATMEE_RENDER=1 \\
    ATMEE_API_KEY=sk_atmee_... ATMEE_AVATAR_ID=... \\
    LIVEKIT_URL=wss://... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=... \\
    uv run pytest tests/test_live_render.py -m live -s

``ATMEE_API_URL`` targets another environment (staging).
"""

from __future__ import annotations

import array
import asyncio
import math
import os
import uuid

import pytest

REQUIRED_ENV = (
    "ATMEE_API_KEY",
    "ATMEE_AVATAR_ID",
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
)


def _missing() -> list[str]:
    return [name for name in REQUIRED_ENV if not os.getenv(name)]


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_ATMEE_RENDER") != "1",
        reason="live test; set RUN_LIVE_ATMEE_RENDER=1 to run",
    ),
    pytest.mark.skipif(bool(_missing()), reason=f"missing env: {', '.join(_missing())}"),
]

RATE = 16000


def _voice_like(seconds: float) -> bytes:
    """A pitched buzz with syllable-like swells: enough for the face to move."""
    samples = array.array("h")
    for i in range(int(RATE * seconds)):
        t = i / RATE
        swell = 0.5 + 0.5 * math.sin(2 * math.pi * 3.0 * t)
        tone = sum(math.sin(2 * math.pi * 140 * k * t) / k for k in range(1, 6))
        samples.append(int(6000 * swell * tone / 2))
    return samples.tobytes()


async def test_the_avatar_renders_and_plays_what_the_agent_says() -> None:
    from livekit import api, rtc
    from livekit.agents import AgentSession
    from livekit.plugins import atmee

    room_name = f"atmee-live-render-{uuid.uuid4().hex[:8]}"
    token = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity("atmee-live-render-agent")
        .with_kind("agent")
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
        .to_jwt()
    )
    room = rtc.Room()
    counts = {"video": 0, "loud_audio": 0}
    readers: list[asyncio.Task[None]] = []

    async def read_video(track: rtc.Track) -> None:
        async for _ in rtc.VideoStream(track):
            counts["video"] += 1

    async def read_audio(track: rtc.Track) -> None:
        async for ev in rtc.AudioStream(track, sample_rate=RATE, num_channels=1):
            data = ev.frame.data
            if len(data) and max(abs(v) for v in data) > 1000:
                counts["loud_audio"] += 1

    @room.on("track_subscribed")
    def _on_track(track: rtc.Track, *_: object) -> None:
        reader = read_video if track.kind == rtc.TrackKind.KIND_VIDEO else read_audio
        readers.append(asyncio.create_task(reader(track)))

    await room.connect(os.environ["LIVEKIT_URL"], token)
    avatar = atmee.AvatarSession(
        avatar_id=os.environ["ATMEE_AVATAR_ID"],
        wait_for="avatar_joined",
        max_duration_seconds=300,
    )
    session: AgentSession = AgentSession()
    try:
        await avatar.start(session, room)
        assert avatar.session_info is not None
        assert avatar.session_info.status == "avatar_joined"

        output = session.output.audio
        assert output is not None
        played: dict[str, object] = {}
        finished = asyncio.Event()

        def _on_finished(ev: object) -> None:
            played.update(position=ev.playback_position, interrupted=ev.interrupted)  # type: ignore[attr-defined]
            finished.set()

        output.on("playback_started", lambda _ev: played.setdefault("started", True))
        output.on("playback_finished", _on_finished)
        output.on_attached()
        pcm = _voice_like(4.0)
        chunk = RATE // 10 * 2
        for at in range(0, len(pcm), chunk):
            piece = pcm[at : at + chunk]
            await output.capture_frame(rtc.AudioFrame(piece, RATE, 1, len(piece) // 2))
        output.flush()

        await asyncio.wait_for(finished.wait(), timeout=60)
        await asyncio.sleep(1.0)
        assert played.get("started") is True
        assert played.get("interrupted") is False
        assert float(played["position"]) >= 3.5  # type: ignore[arg-type]
        assert counts["video"] > 50, counts
        assert counts["loud_audio"] > 100, counts
    finally:
        session_id = avatar.session_id
        await avatar.aclose()
        for reader in readers:
            reader.cancel()
        await room.disconnect()

    async with atmee.AtmeeAPI() as client:
        for _ in range(20):
            settled = await client.get_avatar_session(session_id)  # type: ignore[arg-type]
            if settled.get("status") == "completed":
                break
            await asyncio.sleep(0.5)
        assert settled.get("status") == "completed", settled
