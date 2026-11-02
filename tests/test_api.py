from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from livekit.agents import APIConnectOptions
from livekit.plugins import atmee
from livekit.plugins.atmee import (
    CREATABLE_AVATAR_VERSIONS,
    SUPPORTED_AVATAR_VERSIONS,
    AtmeeAPI,
    AtmeeAvatarNotReadyError,
    AtmeeException,
    AtmeeNoCapacityError,
)
from livekit.plugins.atmee import api as atmee_api

from .conftest import API_KEY, AVATAR_ID, SESSION_ID, FakeAtmee

SESSIONS_PATH = f"/v1/avatars/{AVATAR_ID}/avatar_sessions"
FAST = APIConnectOptions(max_retry=3, retry_interval=0.0, timeout=5.0)


def _start_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "sessionId": SESSION_ID,
        "status": "initializing",
        "avatarParticipantIdentity": "atmee-avatar-agent",
        "agentIdentity": "my-agent",
        "roomName": "dev-room-42",
        "maxDurationSeconds": 3600,
        "billingMode": "metered",
    }
    body.update(overrides)
    return body


async def test_create_avatar_session_sends_key_and_payload(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    fake_atmee.script("POST", SESSIONS_PATH, 202, _start_body())
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    info = await api.create_avatar_session(
        AVATAR_ID,
        livekit_url="wss://dev.livekit.cloud",
        livekit_token="eyJ.tok",
        agent_identity="my-agent",
        max_duration_seconds=1800,
        metadata={"tenant": "t1"},
    )
    call = fake_atmee.calls("POST", SESSIONS_PATH)[0]
    assert call.headers["X-Api-Key"] == API_KEY
    assert call.json == {
        "livekitUrl": "wss://dev.livekit.cloud",
        "livekitToken": "eyJ.tok",
        "agentIdentity": "my-agent",
        "maxDurationSeconds": 1800,
        "metadata": {"tenant": "t1"},
    }
    assert call.query == {}
    assert info.session_id == SESSION_ID
    assert info.status == "initializing"
    assert info.agent_identity == "my-agent"
    assert info.room_name == "dev-room-42"
    assert info.max_duration_seconds == 3600
    assert info.billing_mode == "metered"
    assert info.avatar_version == "v1"
    assert api.api_url == fake_atmee.url


def test_v1_and_v2_avatars_are_rendered_and_v1_created() -> None:
    assert SUPPORTED_AVATAR_VERSIONS == frozenset({"v1", "v2"})
    assert CREATABLE_AVATAR_VERSIONS == frozenset({"v1"})


async def test_create_avatar_session_explicit_v1_sends_no_version_field(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    # The API contract has no version field: the version is plugin-side only
    # and the request body is byte-for-byte what it was before.
    fake_atmee.script("POST", SESSIONS_PATH, 202, _start_body())
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    info = await api.create_avatar_session(
        AVATAR_ID, livekit_url="wss://x", livekit_token="t", avatar_version="v1"
    )
    assert info.avatar_version == "v1"
    assert fake_atmee.calls("POST", SESSIONS_PATH)[0].json == {
        "livekitUrl": "wss://x",
        "livekitToken": "t",
    }


async def test_create_avatar_session_v2_sends_the_same_request(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    # The avatar's own configuration decides how it renders: a v2 start is the
    # same request, and the version is the plugin's (its start budget).
    fake_atmee.script("POST", SESSIONS_PATH, 202, _start_body())
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    info = await api.create_avatar_session(
        AVATAR_ID, livekit_url="wss://x", livekit_token="t", avatar_version="v2"
    )
    assert info.avatar_version == "v2"
    assert fake_atmee.calls("POST", SESSIONS_PATH)[0].json == {
        "livekitUrl": "wss://x",
        "livekitToken": "t",
    }


def _warming(monkeypatch: pytest.MonkeyPatch, budgets: dict[str, float] | None = None) -> None:
    """Ask again at once after a warming answer, within small start budgets."""
    monkeypatch.setattr(atmee_api, "_WARMING_RETRY_BOUNDS", (0.005, 0.01))
    if budgets is not None:
        monkeypatch.setattr(
            atmee_api,
            "_START_TOTAL_TIMEOUT",
            {v: {"initializing": t, "avatar_joined": t} for v, t in budgets.items()},
        )


@pytest.mark.parametrize("code", ["avatar_warming", "avatar_start_interrupted"])
async def test_a_render_that_could_not_start_yet_is_asked_again(
    fake_atmee: FakeAtmee,
    http_session: aiohttp.ClientSession,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
) -> None:
    # Nothing was reserved: the start is asked again after its Retry-After.
    _warming(monkeypatch)
    for _ in range(2):
        fake_atmee.script(
            "POST",
            SESSIONS_PATH,
            503,
            {"error": code, "message": "starting"},
            headers={"Retry-After": "0"},
        )
    fake_atmee.script("POST", SESSIONS_PATH, 202, _start_body())
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    info = await api.create_avatar_session(
        AVATAR_ID, livekit_url="wss://x", livekit_token="t", avatar_version="v2"
    )
    assert info.session_id == SESSION_ID
    assert len(fake_atmee.calls("POST", SESSIONS_PATH)) == 3


async def test_a_v2_avatar_gets_the_longer_start_budget(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The same pool, warming for longer than a v1 start may wait: declared v2,
    # the start rides it out; declared v1, it gives up and says what to pass.
    _warming(monkeypatch, {"v1": 0.05, "v2": 5.0})
    warming = {"error": "avatar_warming", "message": "the v2 render pool is starting"}
    fake_atmee.script("POST", SESSIONS_PATH, 503, warming, headers={"Retry-After": "0"}, times=200)
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    with pytest.raises(atmee.AtmeeAvatarWarmingError, match="pass avatar_version='v2'") as exc:
        await api.create_avatar_session(AVATAR_ID, livekit_url="wss://x", livekit_token="t")
    assert exc.value.code == "avatar_warming" and exc.value.status_code == 503

    fake_atmee.scripts.clear()
    fake_atmee.requests.clear()
    for _ in range(5):
        fake_atmee.script("POST", SESSIONS_PATH, 503, warming, headers={"Retry-After": "0"})
    fake_atmee.script("POST", SESSIONS_PATH, 202, _start_body())
    info = await api.create_avatar_session(
        AVATAR_ID, livekit_url="wss://x", livekit_token="t", avatar_version="v2"
    )
    assert info.session_id == SESSION_ID
    assert len(fake_atmee.calls("POST", SESSIONS_PATH)) == 6


async def test_a_failed_or_unanswered_start_is_never_asked_again(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A 5xx that is no warming answer (a proxy's 504, a server error) may have
    # reserved a render: asking again could open a second one.
    _warming(monkeypatch)
    fake_atmee.script("POST", SESSIONS_PATH, 504, body="Gateway Timeout")
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    with pytest.raises(atmee.AtmeeException) as exc:
        await api.create_avatar_session(
            AVATAR_ID, livekit_url="wss://x", livekit_token="t", avatar_version="v2"
        )
    assert not isinstance(exc.value, atmee.AtmeeAvatarWarmingError)
    assert len(fake_atmee.calls("POST", SESSIONS_PATH)) == 1


async def test_create_avatar_session_wait_for_avatar_joined(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    fake_atmee.script("POST", SESSIONS_PATH, 202, _start_body(status="avatar_joined"))
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    info = await api.create_avatar_session(
        AVATAR_ID, livekit_url="wss://x", livekit_token="t", wait_for="avatar_joined"
    )
    assert info.status == "avatar_joined"
    assert fake_atmee.calls("POST", SESSIONS_PATH)[0].query == {"waitFor": "avatar_joined"}


async def test_no_capacity_is_typed_and_never_retried(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    fake_atmee.script(
        "POST",
        SESSIONS_PATH,
        503,
        {"error": "no_capacity", "message": "no rendering capacity right now"},
        headers={"Retry-After": "5"},
        times=3,
    )
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    with pytest.raises(AtmeeNoCapacityError) as exc:
        await api.create_avatar_session(AVATAR_ID, livekit_url="wss://x", livekit_token="t")
    assert len(fake_atmee.calls("POST", SESSIONS_PATH)) == 1
    assert exc.value.retry_after == 5.0
    assert exc.value.code == "no_capacity"
    assert exc.value.status_code == 503


async def test_4xx_is_final_with_code(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    fake_atmee.script(
        "POST",
        SESSIONS_PATH,
        400,
        {"error": "missing_publish_on_behalf", "message": "token lacks the attribute"},
        times=3,
    )
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    with pytest.raises(AtmeeException) as exc:
        await api.create_avatar_session(AVATAR_ID, livekit_url="wss://x", livekit_token="t")
    assert len(fake_atmee.calls("POST", SESSIONS_PATH)) == 1
    assert exc.value.code == "missing_publish_on_behalf"
    assert exc.value.status_code == 400
    assert "token lacks the attribute" in str(exc.value)


async def test_not_renderable_is_typed(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    fake_atmee.script(
        "POST", SESSIONS_PATH, 409, {"error": "avatar_not_renderable", "message": "no portrait"}
    )
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    with pytest.raises(AtmeeAvatarNotReadyError):
        await api.create_avatar_session(AVATAR_ID, livekit_url="wss://x", livekit_token="t")


async def test_session_create_is_never_retried(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    # The POST bills a session once the server accepts it; a retry after a
    # timeout or 5xx could open a second render, so it is final either way.
    fake_atmee.script(
        "POST", SESSIONS_PATH, 502, {"error": "avatar_start_failed", "message": "no face"}
    )
    fake_atmee.script("POST", SESSIONS_PATH, 202, _start_body())
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    with pytest.raises(AtmeeException) as exc:
        await api.create_avatar_session(AVATAR_ID, livekit_url="wss://x", livekit_token="t")
    assert exc.value.status_code == 502 and exc.value.code == "avatar_start_failed"
    assert len(fake_atmee.calls("POST", SESSIONS_PATH)) == 1


async def test_idempotent_calls_retry_5xx_then_raise(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    get_path = f"/v1/avatar_sessions/{SESSION_ID}"
    fake_atmee.script("GET", get_path, 502, body="bad gateway")
    fake_atmee.script("GET", get_path, 500, body="oops")
    fake_atmee.script("GET", get_path, 200, {"sessionId": SESSION_ID, "status": "active"})
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    got = await api.get_avatar_session(SESSION_ID)
    assert len(fake_atmee.calls("GET", get_path)) == 3
    assert got["status"] == "active"

    # max_retry=3 means three retries after the first attempt: four calls
    fake_atmee.script("GET", get_path, 500, body="down", times=4)
    with pytest.raises(AtmeeException) as exc:
        await api.get_avatar_session(SESSION_ID)
    assert exc.value.status_code == 500
    assert len(fake_atmee.calls("GET", get_path)) == 7


async def test_end_and_get_avatar_session(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    end_path = f"/v1/avatar_sessions/{SESSION_ID}/end"
    get_path = f"/v1/avatar_sessions/{SESSION_ID}"
    fake_atmee.script(
        "POST",
        end_path,
        200,
        {"sessionId": SESSION_ID, "status": "completed", "alreadyEnded": True},
    )
    fake_atmee.script("GET", get_path, 200, {"sessionId": SESSION_ID, "status": "completed"})
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    ended = await api.end_avatar_session(SESSION_ID)
    got = await api.get_avatar_session(SESSION_ID)
    assert fake_atmee.calls("POST", end_path)[0].headers["X-Api-Key"] == API_KEY
    assert ended["alreadyEnded"] is True
    assert got["status"] == "completed"


async def test_create_avatar_from_file_is_multipart(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession, tmp_path: Path
) -> None:
    portrait = tmp_path / "val.png"
    portrait.write_bytes(b"\x89PNG\r\n\x1a\nfakepng")
    fake_atmee.script(
        "POST",
        "/v1/avatars",
        201,
        {"avatarId": AVATAR_ID, "kind": "render_only", "status": "ready", "statusUrl": "x"},
    )
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    avatar_id = await api.create_avatar("Val", portrait, description="from a test")
    assert avatar_id == AVATAR_ID
    call = fake_atmee.calls("POST", "/v1/avatars")[0]
    assert call.form["name"] == "Val"
    assert call.form["description"] == "from a test"
    assert call.form["file"]["filename"] == "val.png"
    assert call.form["file"]["content_type"] == "image/png"
    assert call.form["file"]["data"] == b"\x89PNG\r\n\x1a\nfakepng"
    assert set(call.form) == {"name", "description", "file"}  # no version field


async def test_create_avatar_reports_v1_by_default(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    fake_atmee.script(
        "POST",
        "/v1/avatars",
        201,
        {"avatarId": AVATAR_ID, "kind": "render_only", "status": "ready"},
    )
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    info = await api.create_avatar_info("Val", b"jpegbytes")
    # `kind` comes from the API (voice/persona or not); `version` is the avatar
    # generation, filled in by the plugin because the API reports none.
    assert info.kind == "render_only"
    assert info.version == "v1"
    assert "version" not in info.raw
    assert set(fake_atmee.calls("POST", "/v1/avatars")[0].form) == {"name", "file"}


async def test_create_avatar_explicit_v1_keeps_manifest_unchanged(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    fake_atmee.script("POST", "/v1/avatars", 201, {"avatarId": AVATAR_ID, "status": "ready"})
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    info = await api.create_avatar_info("Val", "https://cdn.example/val.jpg", avatar_version="v1")
    assert info.version == "v1"
    assert fake_atmee.calls("POST", "/v1/avatars")[0].json == {
        "schemaVersion": 1,
        "name": "Val",
        "assets": {"image": {"url": "https://cdn.example/val.jpg"}},
    }


async def test_create_avatar_rejects_v2_before_any_request(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession, tmp_path: Path
) -> None:
    # A v2 avatar needs a voice and a take built over minutes: made elsewhere, rendered here.
    fake_atmee.script("POST", "/v1/avatars", 201, {"avatarId": AVATAR_ID, "status": "ready"})
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    with pytest.raises(ValueError, match="create_avatar makes v1 avatars only"):
        await api.create_avatar("Val", "https://cdn.example/val.jpg", avatar_version="v2")
    with pytest.raises(ValueError, match="Atmee studio, or with the Atmee CLI or MCP server"):
        await api.create_avatar_info("Val", b"jpegbytes", avatar_version="v2")
    missing = tmp_path / "does-not-exist.png"  # never read: the version check comes first
    with pytest.raises(ValueError):
        await api.create_avatar("Val", missing, avatar_version="v2")
    assert fake_atmee.requests == []


async def test_create_avatar_from_bytes(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    fake_atmee.script("POST", "/v1/avatars", 201, {"avatarId": AVATAR_ID, "status": "ready"})
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    await api.create_avatar("Val", b"webpbytes", content_type="image/webp")
    part = fake_atmee.calls("POST", "/v1/avatars")[0].form["file"]
    assert part["filename"] == "portrait.webp" and part["content_type"] == "image/webp"


async def test_create_avatar_from_url_is_json_manifest(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    fake_atmee.script(
        "POST",
        "/v1/avatars",
        201,
        {"avatarId": AVATAR_ID, "kind": "render_only", "status": "ready", "statusUrl": "x"},
    )
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    info = await api.create_avatar_info("Val", "https://cdn.example/val.jpg")
    assert fake_atmee.calls("POST", "/v1/avatars")[0].json == {
        "schemaVersion": 1,
        "name": "Val",
        "assets": {"image": {"url": "https://cdn.example/val.jpg"}},
    }
    assert info.ready and info.kind == "render_only"


async def test_wait_until_ready_polls(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    path = f"/v1/avatars/{AVATAR_ID}"
    fake_atmee.script("GET", path, 200, {"avatarId": AVATAR_ID, "status": "building"})
    fake_atmee.script(
        "GET", path, 200, {"avatarId": AVATAR_ID, "status": "ready", "kind": "conversational"}
    )
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    info = await api.wait_until_ready(AVATAR_ID, timeout=10, poll_interval=0)
    assert info.ready and info.kind == "conversational"
    assert len(fake_atmee.calls("GET", path)) == 2


def test_missing_api_key_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATMEE_API_KEY")
    with pytest.raises(AtmeeException):
        AtmeeAPI()


async def test_owns_session_outside_a_job(fake_atmee: FakeAtmee) -> None:
    fake_atmee.script(
        "GET", f"/v1/avatars/{AVATAR_ID}", 200, {"avatarId": AVATAR_ID, "status": "ready"}
    )
    api = AtmeeAPI(conn_options=FAST)
    async with api:
        info = await api.get_avatar(AVATAR_ID)
    assert info.ready
    assert api._session is None  # closed and dropped by aclose()


def test_error_str_carries_code_and_status() -> None:
    e = AtmeeException("nope", status_code=402, code="insufficient_credits")
    assert str(e) == "nope [insufficient_credits] (HTTP 402)"


def test_plaintext_api_url_is_refused() -> None:
    with pytest.raises(AtmeeException, match="https"):
        AtmeeAPI(api_url="http://api.example.com")
    AtmeeAPI(api_url="http://127.0.0.1:8080")  # loopback is fine for local development
    AtmeeAPI(api_url="https://api.example.com")


async def test_wait_until_ready_honours_its_timeout(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    path = f"/v1/avatars/{AVATAR_ID}"
    fake_atmee.script("GET", path, 200, {"avatarId": AVATAR_ID, "status": "building"}, times=5)
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    loop = asyncio.get_running_loop()
    began = loop.time()
    with pytest.raises(AtmeeException) as exc:
        await api.wait_until_ready(AVATAR_ID, timeout=0.2, poll_interval=30)
    assert exc.value.code == "timeout"
    assert loop.time() - began < 2  # the 30 s poll interval was capped by the deadline


async def test_malformed_success_is_a_typed_error(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    get_path = f"/v1/avatar_sessions/{SESSION_ID}"
    fake_atmee.script("GET", get_path, 200, body="<html>not json</html>")
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    with pytest.raises(AtmeeException) as exc:
        await api.get_avatar_session(SESSION_ID)
    assert exc.value.code == "invalid_response"
    assert len(fake_atmee.calls("GET", get_path)) == 1  # a 2xx is never retried


async def test_wait_until_ready_deadline_bounds_retries(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    path = f"/v1/avatars/{AVATAR_ID}"
    fake_atmee.script("GET", path, 502, body="bad gateway", times=20)
    slow_retries = APIConnectOptions(max_retry=5, retry_interval=30.0, timeout=5.0)
    api = AtmeeAPI(session=http_session, conn_options=slow_retries)
    loop = asyncio.get_running_loop()
    began = loop.time()
    with pytest.raises(AtmeeException) as exc:
        await api.wait_until_ready(AVATAR_ID, timeout=0.3, poll_interval=30)
    # deadline expiry is a timeout, not the last 5xx seen before it
    assert exc.value.code == "timeout"
    # the 30 s retry pauses were cut to the remaining budget
    assert loop.time() - began < 2


async def test_plain_503_is_retried_like_any_5xx(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession
) -> None:
    get_path = f"/v1/avatar_sessions/{SESSION_ID}"
    fake_atmee.script("GET", get_path, 503, body="Service Unavailable")  # e.g. from a proxy
    fake_atmee.script("GET", get_path, 200, {"sessionId": SESSION_ID, "status": "active"})
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    got = await api.get_avatar_session(SESSION_ID)
    assert got["status"] == "active"
    assert len(fake_atmee.calls("GET", get_path)) == 2


def test_an_avatars_version_is_its_engine_and_a_v2_is_ready_once_it_serves_a_take() -> None:
    v1 = atmee_api._avatar_info({"avatarId": AVATAR_ID, "status": "ready"})
    assert v1.version == "v1" and v1.ready
    building = atmee_api._avatar_info({"avatarId": AVATAR_ID, "status": "ready", "engine": "v2"})
    assert building.version == "v2" and not building.ready  # no take serving yet
    serving = atmee_api._avatar_info(
        {"avatarId": AVATAR_ID, "status": "ready", "engine": "v2", "servingCandidateId": "c1"}
    )
    assert serving.ready


def test_a_v2_start_may_wait_as_long_as_the_api_says_a_cold_pool_takes() -> None:
    # The service waits up to 4 minutes for a v2 pool to take the render and up
    # to 6 for the avatar to join, and asks clients for at least 7 minutes.
    budgets = atmee_api._START_TOTAL_TIMEOUT
    assert budgets["v2"]["initializing"] >= 4 * 60
    assert budgets["v2"]["avatar_joined"] >= 7 * 60
    assert budgets["v1"]["initializing"] < budgets["v2"]["initializing"]
