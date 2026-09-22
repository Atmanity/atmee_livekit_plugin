# AGENTS.md — fast setup for humans and coding agents

`livekit-plugins-atmee` renders an Atmee **v1 avatar** — a talking head generated
from a single portrait — into a LiveKit room, driven by your LiveKit voice agent's
speech. The plugin targets v1 avatars specifically: `AvatarSession(avatar_version=...)`
defaults to `"v1"`, and `"v2"` (the next avatar generation, not yet available through
the plugin) raises `ValueError`. This file gets you from clone to a talking avatar in
minutes; the README has the full API.

## 1. Setup (2 minutes)

```bash
uv venv && uv pip install -e . --group dev       # dev + examples deps (uv default-groups)
cp examples/.env.example .env                     # then fill it in (see below)
```

Python 3.10+. Everything reads `.env` via `python-dotenv`.

| variable | needed for | where from |
|---|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | everything | your LiveKit Cloud project (or self-hosted LiveKit server) |
| `ATMEE_API_KEY` (`sk_atmee_...`) | rendering | create at https://www.atmee.ai/studio/api-keys |
| `ATMEE_AVATAR_ID` | rendering | `uv run python examples/create_avatar.py <portrait.jpg>` prints one |
| `ATMEE_API_URL` | *optional* | defaults to production `https://api.atmanity.us`; set only to target another environment |
| `OPENAI_API_KEY` | both example agents | the gpt-live-1 example needs gpt-live-1 access |
| `DEEPGRAM_API_KEY` | `examples/agent.py` (STT) | deepgram.com |

The plugin never sends your LiveKit secret anywhere: it mints a room-scoped token
for the avatar participant locally and hands only that token to Atmee.

## 2. Run something (pick one)

**A. Classic STT → LLM → TTS agent** (Deepgram + OpenAI + Silero):

```bash
uv run python examples/agent.py dev
# open https://agents-playground.livekit.io, connect to your project, talk
```

**B. OpenAI gpt-live-1** (full-duplex realtime voice: backchannels, can sing):

```bash
uv run python examples/gpt_live_1_agent.py dev          # terminal 1: the worker
uv run python examples/dispatch.py                      # terminal 2: prints a viewer URL, dispatches
```

The gpt-live-1 worker uses **explicit dispatch** (`agent_name="gpt-live-1-demo"`), so
it only joins rooms it is dispatched to — never every room of a shared project. Keep
it that way.

## 3. Know it works

The avatar participant `atmee-avatar-agent` joins your room, publishes video, and
its lips move when the agent speaks. In the viewer, the avatar's audio comes **from
the avatar participant**, not from your agent (the plugin reroutes the agent's audio
to the avatar). Billing runs while the avatar is in the room and stops when your
agent leaves, the room closes, or `AvatarSession.aclose()` runs.

## 4. Gotchas

- **Start the avatar before the agent session** (`await avatar.start(session,
  room=...)` then `await session.start(...)`) so the agent's audio is routed to the
  avatar instead of being published directly. The examples show the order.
- **Explicit dispatch on shared projects.** A worker without `agent_name`
  auto-dispatches into every room of the LiveKit project.
- **gpt-live-1 delegation:** the example defaults to `delegation="client"` — gpt-live-1
  listens, decides and speaks by itself, so it can backchannel mid-sentence and answer
  with the lowest latency; that immediacy (plus the prompt) is what makes it feel alive.
  `GPT_LIVE_DELEGATION=responses` adds a backend reasoning model: smarter and able to
  speak unprompted, but an extra round-trip on every turn — measurably less lively on a
  live call. Only a Responses-delegated session accepts an unprompted
  `generate_reply` / `response.create`; under `client` it greets when it first hears you.
- **Shared OpenAI keys can get gpt-live-1 policy-blocked mid-session**; use a
  dedicated key for demos.
- **Engagement is the prompt, not the voice model.** A bare gpt-live-1 or a bare
  STT/LLM/TTS prompt feels passive; the expressive instructions in the examples are
  what make the avatar feel alive.
- **Errors you may see:** `AtmeeException` when `ATMEE_API_KEY` is missing (create one
  at https://www.atmee.ai/studio/api-keys); `api_key_spend_cap_reached` (HTTP 402) when
  the key's spend cap is used up — the agent joins and immediately leaves because the
  avatar session is refused; raise the cap or use another key in the studio; a
  not-render-ready avatar when it has no portrait or is still processing; a capacity
  error (`retry_after`) when Atmee is busy — back off, don't hammer.

## 5. Repo map

```
livekit/plugins/atmee/   the plugin: AvatarSession (avatar token + Atmee API + audio routing), AtmeeAPI
examples/                runnable demos (see above) + .env.example
tests/                   unit tests; tests/test_live_agent.py = manual live smoke test (RUN_LIVE_ATMEE_AGENT=1)
```

## 6. Before you commit

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q
```

Line length 100, `ruff` rules `E F I B UP ASYNC`, `mypy --strict` on the plugin
package. Examples carry the Apache-2.0 header and a docstring that says how to run
them. Keep demos under `examples/`; this repo is public — never reference Atmee's
internal services, hosts or infrastructure in it.
