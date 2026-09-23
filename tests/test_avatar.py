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
    fake_atmee.script("POST", END_PATH, 500, body="down")  # max_retry=1: two attempts
    await avatar.aclose()  # must not raise
    assert len(fake_atmee.calls("POST", END_PATH)) == 2

    # the failed end was not recorded as done, so a later aclose retries it
    fake_atmee.script("POST", END_PATH, 200, {"sessionId": SESSION_ID, "alreadyEnded": False})
    await avatar.aclose()
    assert len(fake_atmee.calls("POST", END_PATH)) == 3
    await avatar.aclose()  # confirmed ended: no further request
    assert len(fake_atmee.calls("POST", END_PATH)) == 3


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


async def test_avatar_version_defaults_to_v1(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    fake_atmee.script("POST", SESSIONS_PATH, 202, _start_body())
    fake_atmee.script("POST", END_PATH, 200, {"sessionId": SESSION_ID, "status": "completed"})
    avatar = atmee.AvatarSession(avatar_id=AVATAR_ID, conn_options=FAST, http_session=http_session)
    assert avatar.avatar_version == "v1"

    await avatar.start(FakeAgentSession(), FakeRoom())  # type: ignore[arg-type]

    assert avatar.session_info is not None
    assert avatar.session_info.avatar_version == "v1"
    # The API has no version field: the request body is unchanged.
    body = fake_atmee.calls("POST", SESSIONS_PATH)[0].json
    assert set(body) == {"livekitUrl", "livekitToken", "agentIdentity", "maxDurationSeconds"}
    await avatar.aclose()


async def test_explicit_v1_is_accepted(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    fake_atmee.script("POST", SESSIONS_PATH, 202, _start_body())
    fake_atmee.script("POST", END_PATH, 200, {"sessionId": SESSION_ID, "status": "completed"})
    avatar = atmee.AvatarSession(
        avatar_id=AVATAR_ID, conn_options=FAST, http_session=http_session, avatar_version="v1"
    )
    await avatar.start(FakeAgentSession(), FakeRoom())  # type: ignore[arg-type]
    assert avatar.avatar_version == "v1"
    assert avatar.session_info is not None and avatar.session_info.avatar_version == "v1"
    assert "avatarVersion" not in fake_atmee.calls("POST", SESSIONS_PATH)[0].json
    await avatar.aclose()


def test_v2_is_rejected_at_construction(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    # v2 is the next avatar generation and not available through the plugin
    # yet: the constructor says so, before any token is minted or request sent.
    with pytest.raises(ValueError, match="avatar_version 'v2' is not supported") as exc:
        atmee.AvatarSession(
            avatar_id=AVATAR_ID, conn_options=FAST, http_session=http_session, avatar_version="v2"
        )
    assert "only 'v1'" in str(exc.value)
    assert fake_atmee.requests == []


def test_unknown_version_is_rejected_at_construction(http_session: aiohttp.ClientSession) -> None:
    with pytest.raises(ValueError, match="avatar_version 'v3' is not supported"):
        atmee.AvatarSession(
            avatar_id=AVATAR_ID,
            conn_options=FAST,
            http_session=http_session,
            avatar_version="v3",  # type: ignore[arg-type]
        )


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


async def test_end_4xx_is_final(fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession) -> None:
    fake_atmee.script("POST", SESSIONS_PATH, 202, _start_body())
    fake_atmee.script("POST", END_PATH, 404, {"error": "not_found", "message": "gone"})
    avatar = atmee.AvatarSession(avatar_id=AVATAR_ID, conn_options=FAST, http_session=http_session)
    await avatar.start(FakeAgentSession(), FakeRoom())  # type: ignore[arg-type]
    await avatar.aclose()
    await avatar.aclose()  # unknown session: nothing left to end, no retry
    assert len(fake_atmee.calls("POST", END_PATH)) == 1


async def test_start_is_one_shot(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    fake_atmee.script("POST", SESSIONS_PATH, 202, _start_body())
    avatar = atmee.AvatarSession(avatar_id=AVATAR_ID, conn_options=FAST, http_session=http_session)
    await avatar.start(FakeAgentSession(), FakeRoom())  # type: ignore[arg-type]
    with pytest.raises(atmee.AtmeeException, match="already called"):
        await avatar.start(FakeAgentSession(), FakeRoom())  # type: ignore[arg-type]
    # the second call never reached the API, so no second billed render
    assert len(fake_atmee.calls("POST", SESSIONS_PATH)) == 1


async def test_start_without_session_id_is_rejected(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    fake_atmee.script("POST", SESSIONS_PATH, 202, {"status": "initializing"})
    avatar = atmee.AvatarSession(avatar_id=AVATAR_ID, conn_options=FAST, http_session=http_session)
    with pytest.raises(atmee.AtmeeException) as exc:
        await avatar.start(FakeAgentSession(), FakeRoom())  # type: ignore[arg-type]
    assert exc.value.code == "invalid_response"
    assert avatar.session_id is None


async def test_construct_outside_a_job_without_http_session(fake_atmee: FakeAtmee) -> None:
    # no job context and no session passed: construction must not touch the
    # job's http context; the client creates (and aclose releases) its own
    avatar = atmee.AvatarSession(avatar_id=AVATAR_ID, conn_options=FAST)
    fake_atmee.script("POST", SESSIONS_PATH, 202, _start_body())
    fake_atmee.script("POST", END_PATH, 200, {"sessionId": SESSION_ID})
    await avatar.start(FakeAgentSession(), FakeRoom())  # type: ignore[arg-type]
    await avatar.aclose()
    assert len(fake_atmee.calls("POST", END_PATH)) == 1


async def test_agent_session_close_ends_the_render(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    fake_atmee.script("POST", SESSIONS_PATH, 202, _start_body())
    fake_atmee.script("POST", END_PATH, 200, {"sessionId": SESSION_ID})
    avatar = atmee.AvatarSession(avatar_id=AVATAR_ID, conn_options=FAST, http_session=http_session)
    agent_session = FakeAgentSession()
    await avatar.start(agent_session, FakeRoom())  # type: ignore[arg-type]
    # AgentSession.aclose() without a job shutdown: the render must end too
    for handler in list(agent_session.handlers.get("close", [])):
        handler(None)
    await settle()
    assert len(fake_atmee.calls("POST", END_PATH)) == 1
    assert "close" not in agent_session.handlers or not agent_session.handlers["close"]


async def test_concurrent_aclose_never_closes_the_session_mid_end(
    fake_atmee: FakeAtmee, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_atmee.script("POST", SESSIONS_PATH, 202, _start_body())
    fake_atmee.script("POST", END_PATH, 500, body="down")
    fake_atmee.script("POST", END_PATH, 200, {"sessionId": SESSION_ID})
    no_retry = APIConnectOptions(max_retry=0, retry_interval=0.0, timeout=5.0)
    # no http_session passed: the client owns one, and aclose() closes it
    avatar = atmee.AvatarSession(avatar_id=AVATAR_ID, conn_options=no_retry)
    agent_session = FakeAgentSession()
    await avatar.start(agent_session, FakeRoom())  # type: ignore[arg-type]

    # In a job the base aclose() awaits the LiveKit API to remove the avatar
    # participant, and ending a render is a network round trip: model both,
    # and record how many ends are in flight whenever the HTTP client closes.
    from livekit.agents.voice.avatar import AvatarSession as BaseAvatarSession

    base_aclose = BaseAvatarSession.aclose

    async def slow_base_aclose(self: Any) -> None:
        await asyncio.sleep(0.01)
        await base_aclose(self)

    monkeypatch.setattr(BaseAvatarSession, "aclose", slow_base_aclose)
    in_flight = 0
    in_flight_at_close: list[int] = []
    real_end, real_close = avatar.api.end_avatar_session, avatar.api.aclose

    async def slow_end(session_id: str) -> dict[str, Any]:
        nonlocal in_flight
        in_flight += 1
        try:
            await asyncio.sleep(0.05)
            return await real_end(session_id)
        finally:
            in_flight -= 1

    async def recording_close() -> None:
        in_flight_at_close.append(in_flight)
        await real_close()

    monkeypatch.setattr(avatar.api, "end_avatar_session", slow_end)
    monkeypatch.setattr(avatar.api, "aclose", recording_close)

    # the agent session closes (background aclose) while the job shuts down (explicit aclose)
    for handler in list(agent_session.handlers.get("close", [])):
        handler(None)
    await avatar.aclose()
    for _ in range(100):
        await asyncio.sleep(0.01)
        if len(in_flight_at_close) >= 2:
            break

    assert in_flight_at_close and all(n == 0 for n in in_flight_at_close)
    assert avatar._ended  # the second close retried the failed end and it went through
