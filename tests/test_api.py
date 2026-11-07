from __future__ import annotations

from typing import Any

import aiohttp
import pytest

from livekit.agents import APIConnectOptions
from livekit.plugins import atmee
from livekit.plugins.atmee import (
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
    assert api.api_url == fake_atmee.url


def test_the_0_2_version_names_are_still_importable() -> None:
    assert SUPPORTED_AVATAR_VERSIONS == frozenset({"v1", "v2"})


@pytest.mark.parametrize("avatar_version", ["v1", "v2"])
async def test_avatar_version_is_accepted_ignored_and_warned_about(
    fake_atmee: FakeAtmee,
    http_session: aiohttp.ClientSession,
    caplog: pytest.LogCaptureFixture,
    avatar_version: str,
) -> None:
    # Code written for 0.2.0 keeps working: the avatar's own engine decides how
    # it renders, so the request is the same and names no version.
    fake_atmee.script("POST", SESSIONS_PATH, 202, _start_body())
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    with caplog.at_level("WARNING", logger="livekit.plugins.atmee"):
        info = await api.create_avatar_session(
            AVATAR_ID, livekit_url="wss://x", livekit_token="t", avatar_version=avatar_version
        )
    assert info.session_id == SESSION_ID
    assert fake_atmee.calls("POST", SESSIONS_PATH)[0].json == {
        "livekitUrl": "wss://x",
        "livekitToken": "t",
    }
    assert [r.getMessage() for r in caplog.records].count(
        "avatar_version is deprecated and ignored, the avatar's own engine decides how it renders"
    ) == 1


def _warming(monkeypatch: pytest.MonkeyPatch, budget: float | None = None) -> None:
    """Ask again at once after a warming answer, within small start budgets."""
    monkeypatch.setattr(atmee_api, "_WARMING_RETRY_BOUNDS", (0.005, 0.01))
    if budget is not None:
        monkeypatch.setattr(
            atmee_api,
            "_START_TOTAL_TIMEOUT",
            {g: {"initializing": budget, "avatar_joined": budget} for g in ("v1", "v2")},
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
    info = await api.create_avatar_session(AVATAR_ID, livekit_url="wss://x", livekit_token="t")
    assert info.session_id == SESSION_ID
    assert len(fake_atmee.calls("POST", SESSIONS_PATH)) == 3


async def test_a_start_still_warming_when_its_budget_runs_out_raises(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _warming(monkeypatch, 0.05)
    warming = {"error": "avatar_warming", "message": "the avatar is getting ready"}
    fake_atmee.script("POST", SESSIONS_PATH, 503, warming, headers={"Retry-After": "0"}, times=200)
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    with pytest.raises(atmee.AtmeeAvatarWarmingError) as exc:
        await api.create_avatar_session(AVATAR_ID, livekit_url="wss://x", livekit_token="t")
    assert exc.value.code == "avatar_warming" and exc.value.status_code == 503
    assert len(fake_atmee.calls("POST", SESSIONS_PATH)) >= 2


async def test_a_failed_or_unanswered_start_is_never_asked_again(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A 5xx that is no warming answer (a proxy's 504, a server error) may have
    # reserved a render: asking again could open a second one.
    _warming(monkeypatch)
    fake_atmee.script("POST", SESSIONS_PATH, 504, body="Gateway Timeout")
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    with pytest.raises(atmee.AtmeeException) as exc:
        await api.create_avatar_session(AVATAR_ID, livekit_url="wss://x", livekit_token="t")
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


@pytest.mark.parametrize(
    ("raw", "engine", "ready"),
    [
        ({"status": "ready"}, None, True),
        ({"status": "ready", "engine": "v1"}, "v1", True),
        ({"status": "ready", "engine": "v2"}, "v2", False),  # no take serving yet
        ({"status": "ready", "engine": "v2", "servingCandidateId": "c1"}, "v2", True),
        ({"status": "ready", "engine": "v2hd", "servingCandidateId": "c1"}, "v2hd", True),
        ({"status": "draft", "engine": "v1"}, "v1", False),
    ],
)
def test_an_avatars_engine_and_readiness(
    raw: dict[str, Any], engine: str | None, ready: bool
) -> None:
    info = atmee_api._avatar_info({"avatarId": AVATAR_ID, **raw})
    assert info.engine == engine and info.ready is ready
    assert info.version == (engine or "v1")  # deprecated alias


def test_a_v2_start_may_wait_as_long_as_the_api_asks() -> None:
    # The API asks clients to allow at least 7 minutes for a v2 avatar to join.
    budgets = atmee_api._START_TOTAL_TIMEOUT
    assert budgets["v2"]["initializing"] >= 4 * 60
    assert budgets["v2"]["avatar_joined"] >= 7 * 60
    assert budgets["v1"]["avatar_joined"] < budgets["v2"]["avatar_joined"]


@pytest.mark.parametrize(
    ("avatar", "asks"),
    [
        ((200, {"avatarId": AVATAR_ID, "status": "ready", "engine": "v1"}), 1),
        ((200, {"avatarId": AVATAR_ID, "status": "ready", "engine": "v2hd"}), 3),
        ((403, {"error": "forbidden"}), 3),  # unreadable: allow for a v2 start
    ],
)
async def test_the_start_budget_follows_the_avatars_engine(
    fake_atmee: FakeAtmee,
    http_session: aiohttp.ClientSession,
    monkeypatch: pytest.MonkeyPatch,
    avatar: tuple[int, Any],
    asks: int,
) -> None:
    # A v1 start that is not answered in time gives up after the v1 budget,
    # not the minutes a v2 avatar's first start may need.
    monkeypatch.setattr(atmee_api, "_WARMING_RETRY_BOUNDS", (0.05, 0.05))
    budgets = {"v1": {"initializing": 0.01}, "v2": {"initializing": 5.0}}
    monkeypatch.setattr(atmee_api, "_START_TOTAL_TIMEOUT", budgets)
    fake_atmee.script("GET", f"/v1/avatars/{AVATAR_ID}", *avatar)
    warming = {"error": "avatar_warming", "message": "getting ready"}
    fake_atmee.script("POST", SESSIONS_PATH, 503, warming, times=2)
    fake_atmee.script("POST", SESSIONS_PATH, 202, _start_body())
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    if asks == 1:
        with pytest.raises(atmee.AtmeeAvatarWarmingError):
            await api.create_avatar_session(AVATAR_ID, livekit_url="wss://x", livekit_token="t")
    else:
        info = await api.create_avatar_session(AVATAR_ID, livekit_url="wss://x", livekit_token="t")
        assert info.session_id == SESSION_ID
    assert len(fake_atmee.calls("POST", SESSIONS_PATH)) == asks


@pytest.mark.parametrize("wait_for", ["joined", "", "AVATAR_JOINED"])
async def test_an_unknown_wait_for_is_rejected_before_any_request(
    fake_atmee: FakeAtmee, http_session: aiohttp.ClientSession, wait_for: str
) -> None:
    api = AtmeeAPI(session=http_session, conn_options=FAST)
    with pytest.raises(ValueError, match="wait_for must be one of"):
        await api.create_avatar_session(
            AVATAR_ID,
            livekit_url="wss://x",
            livekit_token="t",
            wait_for=wait_for,  # type: ignore[arg-type]
        )
    assert fake_atmee.requests == []
