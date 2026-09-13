from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer

API_KEY = "sk_atmee_test_key"
AVATAR_ID = "0f0e0d0c-0b0a-4908-8706-050403020100"
SESSION_ID = "11111111-2222-4333-8444-555555555555"
LIVEKIT_SECRET = "the-developers-secret-the-developers-secret"


@dataclass
class Recorded:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    json: Any = None
    form: dict[str, Any] = field(default_factory=dict)


@dataclass
class Scripted:
    status: int
    payload: Any = None
    body: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


class FakeAtmee:
    """A scripted stand-in for the Atmee API: answers each (method, path) from
    a queue of scripted responses and records every request it saw."""

    def __init__(self) -> None:
        self.requests: list[Recorded] = []
        self.scripts: dict[tuple[str, str], deque[Scripted]] = defaultdict(deque)
        self.url = ""

    def script(
        self,
        method: str,
        path: str,
        status: int,
        payload: Any = None,
        *,
        body: str | None = None,
        headers: dict[str, str] | None = None,
        times: int = 1,
    ) -> None:
        for _ in range(times):
            self.scripts[(method, path)].append(Scripted(status, payload, body, headers or {}))

    def calls(self, method: str, path: str) -> list[Recorded]:
        return [r for r in self.requests if r.method == method and r.path == path]

    async def handle(self, request: web.Request) -> web.StreamResponse:
        rec = Recorded(
            method=request.method,
            path=request.path,
            query=dict(request.query),
            headers={k: v for k, v in request.headers.items()},
        )
        ctype = request.content_type
        if ctype == "application/json":
            rec.json = await request.json()
        elif ctype.startswith("multipart/"):
            reader = await request.multipart()
            async for part in reader:
                name = part.name or ""
                if part.filename:
                    rec.form[name] = {
                        "filename": part.filename,
                        "content_type": part.headers.get("Content-Type"),
                        "data": await part.read(decode=False),
                    }
                else:
                    rec.form[name] = (await part.read(decode=False)).decode()
        self.requests.append(rec)

        queue = self.scripts.get((request.method, request.path))
        if not queue:
            return web.json_response({"error": "unscripted", "message": request.path}, status=404)
        s = queue.popleft()
        if s.payload is not None:
            return web.Response(
                status=s.status,
                text=json.dumps(s.payload),
                content_type="application/json",
                headers=s.headers,
            )
        return web.Response(status=s.status, text=s.body or "", headers=s.headers)


@pytest_asyncio.fixture
async def fake_atmee(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FakeAtmee]:
    fa = FakeAtmee()
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", fa.handle)
    server = TestServer(app)
    await server.start_server()
    fa.url = str(server.make_url("")).rstrip("/")
    monkeypatch.setenv("ATMEE_API_URL", fa.url)
    try:
        yield fa
    finally:
        await server.close()


@pytest.fixture(autouse=True)
def _env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    # The live test (marker "live") talks to real LiveKit/Atmee/OpenAI, so it
    # must keep the real environment. Every other test wants the offline stubs.
    if request.node.get_closest_marker("live"):
        return
    monkeypatch.setenv("ATMEE_API_KEY", API_KEY)
    monkeypatch.setenv("ATMEE_API_URL", "https://api.unreachable.test")
    monkeypatch.setenv("LIVEKIT_URL", "wss://dev.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "APIdevkey")
    monkeypatch.setenv("LIVEKIT_API_SECRET", LIVEKIT_SECRET)


@pytest_asyncio.fixture
async def http_session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as session:
        yield session


class FakeParticipant:
    def __init__(self, identity: str) -> None:
        self.identity = identity
        self.name = identity


class FakeRoom:
    """Just enough of rtc.Room for AvatarSession.start(): a name, a local
    participant, event registration, and a disconnected state so nothing
    tries to talk to a server."""

    def __init__(self, name: str = "dev-room-42", local_identity: str = "my-agent") -> None:
        self.name = name
        self.local_participant = FakeParticipant(local_identity)
        self.handlers: dict[str, list[Any]] = {}

    def isconnected(self) -> bool:
        return False

    def on(self, event: str, handler: Any = None) -> Any:
        if handler is None:  # decorator form

            def _register(h: Any) -> Any:
                self.handlers.setdefault(event, []).append(h)
                return h

            return _register
        self.handlers.setdefault(event, []).append(handler)
        return handler

    def off(self, event: str, handler: Any) -> None:
        if handler in self.handlers.get(event, []):
            self.handlers[event].remove(handler)

    def emit(self, event: str, *args: Any) -> None:
        for h in list(self.handlers.get(event, [])):
            h(*args)


class FakeOutput:
    def __init__(self) -> None:
        self.audio_tails: list[Any] = []

    def replace_audio_tail(self, out: Any) -> None:
        self.audio_tails.append(out)


class FakeAgentSession:
    def __init__(self) -> None:
        self.output = FakeOutput()
        self.handlers: dict[str, list[Any]] = {}

    def on(self, event: str, handler: Any) -> Any:
        self.handlers.setdefault(event, []).append(handler)
        return handler

    def off(self, event: str, handler: Any) -> None:
        if handler in self.handlers.get(event, []):
            self.handlers[event].remove(handler)

    def emit(self, *args: Any) -> None:
        pass


async def settle() -> None:
    """Let fire-and-forget tasks run."""
    for _ in range(10):
        await asyncio.sleep(0)
