from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import jwt
import pytest

from livekit import api as lk_api
from livekit.agents import APIConnectOptions
from livekit.agents.voice.avatar import DataStreamAudioOutput
from livekit.plugins import atmee

from .conftest import (
    AVATAR_ID,
    LIVEKIT_SECRET,
    SESSION_ID,
    FakeAgentSession,
    FakeAtmee,
    FakeParticipant,
    FakeRoom,
    settle,
)

SESSIONS_PATH = f"/v1/avatars/{AVATAR_ID}/avatar_sessions"
END_PATH = f"/v1/avatar_sessions/{SESSION_ID}/end"
FAST = APIConnectOptions(max_retry=1, retry_interval=0.0, timeout=5.0)


def _start_body() -> dict[str, Any]:
    return {
        "sessionId": SESSION_ID,
        "status": "initializing",
        "avatarParticipantIdentity": "atmee-avatar-agent",
        "agentIdentity": "my-agent",
        "roomName": "dev-room-42",
        "maxDurationSeconds": 3600,
        "billingMode": "metered",
    }


async def test_start_mints_token_posts_session_and_routes_audio(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    fake_atmee.script("POST", SESSIONS_PATH, 202, _start_body())
    fake_atmee.script("POST", END_PATH, 200, {"sessionId": SESSION_ID, "status": "completed"})
    room = FakeRoom()
    agent_session = FakeAgentSession()
    avatar = atmee.AvatarSession(
        avatar_id=AVATAR_ID, conn_options=FAST, http_session=http_session, metadata={"k": "v"}
    )
    assert avatar.provider == "atmee"
    assert avatar.avatar_identity == "atmee-avatar-agent"

    await avatar.start(agent_session, room)  # type: ignore[arg-type]

    body = fake_atmee.calls("POST", SESSIONS_PATH)[0].json
    assert body["livekitUrl"] == "wss://dev.livekit.cloud"
    assert body["agentIdentity"] == "my-agent"
    assert body["maxDurationSeconds"] == 3600
    assert body["metadata"] == {"k": "v"}

    # The token is minted with the developer's own LiveKit credentials, for the
    # avatar participant, granting exactly this room, publishing on behalf of
    # the local agent.
    claims = lk_api.TokenVerifier("APIdevkey", LIVEKIT_SECRET).verify(body["livekitToken"])
    assert claims.identity == "atmee-avatar-agent"
    # TokenVerifier does not surface `kind`; read the raw payload for it.
    assert jwt.decode(body["livekitToken"], options={"verify_signature": False})["kind"] == "agent"
    assert claims.video is not None and claims.video.room == "dev-room-42"
    assert claims.video.room_join is True
    assert claims.attributes == {"lk.publish_on_behalf": "my-agent"}

    assert avatar.session_id == SESSION_ID
    assert avatar.session_info is not None and avatar.session_info.billing_mode == "metered"

    tails = agent_session.output.audio_tails
    assert len(tails) == 1 and isinstance(tails[0], DataStreamAudioOutput)
    assert tails[0]._destination_identity == "atmee-avatar-agent"
    assert tails[0].sample_rate == atmee.avatar.SAMPLE_RATE == 16000

    await avatar.aclose()
    await avatar.aclose()  # idempotent locally too
    assert len(fake_atmee.calls("POST", END_PATH)) == 1


async def test_avatar_participant_leaving_emits_and_ends_once(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    fake_atmee.script("POST", SESSIONS_PATH, 202, _start_body())
    fake_atmee.script("POST", END_PATH, 200, {"sessionId": SESSION_ID, "status": "completed"})
    room = FakeRoom()
    avatar = atmee.AvatarSession(avatar_id=AVATAR_ID, conn_options=FAST, http_session=http_session)
    seen: list[str] = []
    avatar.on("avatar_disconnected", lambda p: seen.append(p.identity))

    await avatar.start(FakeAgentSession(), room)  # type: ignore[arg-type]

    room.emit("participant_disconnected", FakeParticipant("someone-else"))
    await settle()
    assert seen == []
    room.emit("participant_disconnected", FakeParticipant("atmee-avatar-agent"))
    await settle()
    room.emit("participant_disconnected", FakeParticipant("atmee-avatar-agent"))
    await settle()
    await avatar.aclose()
    assert len(fake_atmee.calls("POST", END_PATH)) == 1
    assert seen == ["atmee-avatar-agent"]


async def test_end_failure_never_breaks_close(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    fake_atmee.script("POST", SESSIONS_PATH, 202, _start_body())
    fake_atmee.script("POST", END_PATH, 500, body="down")
    avatar = atmee.AvatarSession(avatar_id=AVATAR_ID, conn_options=FAST, http_session=http_session)
    await avatar.start(FakeAgentSession(), FakeRoom())  # type: ignore[arg-type]
    await avatar.aclose()  # must not raise
    assert len(fake_atmee.calls("POST", END_PATH)) == 1


async def test_start_failure_is_typed(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    fake_atmee.script(
        "POST",
        SESSIONS_PATH,
        503,
        {"error": "no_capacity", "message": "busy"},
        headers={"Retry-After": "5"},
    )
    avatar = atmee.AvatarSession(avatar_id=AVATAR_ID, conn_options=FAST, http_session=http_session)
    with pytest.raises(atmee.AtmeeNoCapacityError):
        await avatar.start(FakeAgentSession(), FakeRoom())  # type: ignore[arg-type]
    assert avatar.session_id is None
    await avatar.aclose()  # nothing to end
    assert fake_atmee.calls("POST", END_PATH) == []


async def test_requires_livekit_credentials(
    monkeypatch: pytest.MonkeyPatch, http_session: aiohttp.ClientSession
) -> None:
    monkeypatch.delenv("LIVEKIT_API_SECRET")
    avatar = atmee.AvatarSession(avatar_id=AVATAR_ID, conn_options=FAST, http_session=http_session)
    with pytest.raises(atmee.AtmeeException):
        await avatar.start(FakeAgentSession(), FakeRoom())  # type: ignore[arg-type]
    await avatar.aclose()


def test_avatar_id_required() -> None:
    with pytest.raises(atmee.AtmeeException):
        atmee.AvatarSession(avatar_id="")


async def test_wait_for_is_forwarded(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    body = _start_body()
    body["status"] = "avatar_joined"
    fake_atmee.script("POST", SESSIONS_PATH, 202, body)
    fake_atmee.script("POST", END_PATH, 200, {"sessionId": SESSION_ID, "status": "completed"})
    avatar = atmee.AvatarSession(
        avatar_id=AVATAR_ID,
        conn_options=FAST,
        http_session=http_session,
        wait_for="avatar_joined",
        max_duration_seconds=600,
        avatar_participant_identity="val",
    )
    await avatar.start(FakeAgentSession(), FakeRoom())  # type: ignore[arg-type]
    call = fake_atmee.calls("POST", SESSIONS_PATH)[0]
    assert call.query == {"waitFor": "avatar_joined"}
    assert call.json["maxDurationSeconds"] == 600
    claims = lk_api.TokenVerifier("APIdevkey", LIVEKIT_SECRET).verify(call.json["livekitToken"])
    assert claims.identity == "val" and avatar.avatar_identity == "val"
    assert avatar.session_info is not None and avatar.session_info.status == "avatar_joined"
    await asyncio.sleep(0)
    await avatar.aclose()
