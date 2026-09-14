# Atmee avatar plugin for LiveKit Agents

Bring your own LiveKit voice agent; Atmee renders a talking-head avatar from a
single portrait into your room and bills per minute. Works like the other
avatar plugins for [LiveKit Agents](https://docs.livekit.io/agents/): the
avatar joins your room as its own participant, your agent's speech drives its
video.

```python
from livekit.plugins import atmee

avatar_id = await atmee.AtmeeAPI().create_avatar(name="Val", image="portrait.jpg")

avatar = atmee.AvatarSession(avatar_id=avatar_id)   # ATMEE_API_KEY in the environment
await avatar.start(session, room=ctx.room)
await session.start(agent=..., room=ctx.room)       # the agent's TTS drives the avatar's video
```

## Installation

```bash
pip install livekit-plugins-atmee
```

Python 3.10+, `livekit-agents` 1.6.8 or newer.

## Prerequisites

- An **Atmee API key** (`sk_atmee_...`) from your Atmee developer settings, in
  `ATMEE_API_KEY`. Keep it on your agent's side; it never goes to a browser.
- Your **own LiveKit project** (LiveKit Cloud or self-hosted): `LIVEKIT_URL`,
  `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` — the same variables your agent
  already uses. Atmee never sees your LiveKit secret: the plugin mints a token
  for the avatar participant locally and hands only that token to Atmee.

## Usage

### 1. Create an avatar from a portrait

Once, from a script or your backend (see `examples/create_avatar.py`):

```python
async with atmee.AtmeeAPI() as api:
    avatar_id = await api.create_avatar(name="Val", image="portrait.jpg")   # path, bytes, or https URL
```

A portrait-only avatar is ready immediately. You can also create and manage
avatars in the Atmee studio; any avatar of your account with a portrait works.

### 2. Render it in your agent

```python
async def entrypoint(ctx: agents.JobContext):
    await ctx.connect()
    session = AgentSession(stt=..., llm=..., tts=..., vad=...)

    avatar = atmee.AvatarSession(avatar_id=os.environ["ATMEE_AVATAR_ID"])
    await avatar.start(session, room=ctx.room)        # before session.start

    await session.start(agent=MyAgent(), room=ctx.room)
```

`start()` returns as soon as Atmee's rendering worker acknowledged the start
(about a second); the avatar's video and audio tracks appear in the room a few
seconds later, published by the participant `atmee-avatar-agent`. Your agent
publishes no audio of its own — the avatar speaks for it, lips in sync.

Options on `AvatarSession`:

| Argument | Default | Meaning |
|---|---|---|
| `avatar_id` | required | The Atmee avatar to render. |
| `api_key`, `api_url` | `ATMEE_API_KEY`, `ATMEE_API_URL` | Credentials and API base. |
| `avatar_participant_identity` | `atmee-avatar-agent` | Identity the avatar joins with; set it when one room hosts several avatars. |
| `max_duration_seconds` | `3600` | Hard ceiling of the session; also the most it can bill if your agent dies without a trace. |
| `wait_for` | `"initializing"` | `"avatar_joined"` makes `start()` block until the avatar is in the room. |
| `metadata` | `None` | Free-form JSON stored with the session on the Atmee side. |

`avatar.session_id` and `avatar.session_info` hold the Atmee session after
`start()`; `avatar.on("avatar_disconnected", ...)` fires if the avatar
participant leaves while your agent is still running.

### Lifecycle and billing

Billing starts when the avatar sees your agent in the room and runs by the
minute. It stops when

- your agent leaves the room or the room closes (the rendering worker notices
  and reports the end itself — a crashed agent stops being billed within
  seconds),
- `aclose()` runs — registered automatically as a job shutdown callback; it
  ends the session and removes the avatar participant from your room,
- the rendering worker itself dies or loses contact: it proves it is alive
  once a minute, and a session silent for a few minutes is closed and billed
  only up to the last proof,
- or the session reaches `max_duration_seconds`.

### Errors

All API failures raise `atmee.AtmeeException` with `status_code`, `code`
(the API's error code, e.g. `insufficient_credits`, `avatar_not_renderable`,
`missing_publish_on_behalf`) and `message`. Two subclasses are worth
handling: `AtmeeNoCapacityError` (every rendering worker is busy; check
`retry_after` and try again later) and `AtmeeAvatarNotReadyError` (the avatar
has no portrait yet).

## Examples

- [`examples/agent.py`](examples/agent.py) — a Deepgram + OpenAI + Silero agent
  with an Atmee avatar; run with `python examples/agent.py dev` and open the
  LiveKit Agents Playground.
- [`examples/create_avatar.py`](examples/create_avatar.py) — create an avatar
  from a portrait and print its id.
- [`tests/test_live_agent.py`](tests/test_live_agent.py) — a manual live
  smoke test: a real GPT voice agent (OpenAI LLM + TTS, silero VAD) with an
  Atmee avatar in a real LiveKit room, asserting the avatar joins, publishes
  video, and that its agent-driven audio flows. Skipped unless
  `RUN_LIVE_ATMEE_AGENT=1` and the credentials are set:

  ```bash
  RUN_LIVE_ATMEE_AGENT=1 ATMEE_API_KEY=… ATMEE_AVATAR_ID=… \
  LIVEKIT_URL=… LIVEKIT_API_KEY=… LIVEKIT_API_SECRET=… OPENAI_API_KEY=… \
  uv run --group dev --with "livekit-agents[openai,silero]" \
      pytest tests/test_live_agent.py -m live -s
  ```

## How it works

```
your agent (livekit-agents) ── plugin ──► Atmee API        (X-Api-Key, https)
        │                                     │
        │  audio over lk.audio_stream          ▼
        └──────────────► your LiveKit room ◄── Atmee rendering worker
                                              joins as atmee-avatar-agent,
                                              publishes video + audio
```

1. `AvatarSession.start()` mints a LiveKit access token for the avatar
   participant with **your** LiveKit credentials: identity
   `atmee-avatar-agent`, `kind: agent`, a `roomJoin` grant for your room,
   and the attribute `lk.publish_on_behalf` set to your agent's identity.
   Atmee receives only that token, never your secret.
2. It calls `POST /v1/avatars/{avatarId}/avatar_sessions` with your LiveKit
   URL and the token. Atmee reserves a session, starts a rendering worker,
   and answers as soon as the worker acknowledged the start.
3. The worker joins your room with the token, waits for your agent, receives
   its audio over the LiveKit data stream (`lk.audio_stream`, 16 kHz PCM, the
   standard `DataStreamAudioOutput`), and publishes lip-synced video and
   audio on behalf of your agent.
4. When your agent leaves or the room closes, the worker notices from inside
   the room and reports the end to Atmee, which stops billing. `aclose()`
   removes the avatar participant and ends the session explicitly as well.

## Development

```bash
uv venv && uv pip install -e . --group dev
uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Releases are published to PyPI from `v*` tags by `.github/workflows/publish.yml`
(PyPI trusted publishing).

## License

Apache-2.0.
