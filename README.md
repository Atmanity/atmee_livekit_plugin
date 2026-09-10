# atmee_livekit_plugin

LiveKit avatar plugin for Atmee avatars: a developer brings their own LiveKit
voice agent, Atmee renders a talking-head avatar into their room from a single
portrait and bills per minute.

```python
avatar_id = await atmee.AtmeeAPI().create_avatar(name="Val", image="portrait.jpg")

avatar = atmee.AvatarSession(avatar_id=avatar_id)    # ATMEE_API_KEY in env
await avatar.start(session, room=ctx.room)
await session.start(agent=..., room=ctx.room)        # agent TTS → avatar video
```

Status: architecture documented, implementation planned. The published package
will be `livekit-plugins-atmee` (`from livekit.plugins import atmee`).

## Documentation

- [Architecture (arc42)](docs/ARCHITECTURE.md) — the twelve arc42 sections with diagrams, rendered by GitHub.
- [docs/architecture.html](docs/architecture.html) — the same document as a self-contained page (light and dark theme); open it locally or via a Pages deployment.
