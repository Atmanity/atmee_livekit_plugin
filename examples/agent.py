"""A LiveKit voice agent with an Atmee avatar.

    pip install "livekit-agents[deepgram,openai,silero]" livekit-plugins-atmee
    cp examples/.env.example .env   # fill in the keys
    python examples/agent.py dev

This worker uses **explicit dispatch** (`agent_name` is set below), so it only
joins rooms it is explicitly dispatched to — never every room in your project.
On a shared/production project a nameless worker would auto-join live sessions it
shouldn't. Dispatch it to one room and connect a viewer there, e.g.:

    python examples/dispatch.py atmee-cascade-demo

The avatar joins as `atmee-avatar-agent` and speaks the agent's replies. (Remove
`agent_name` below to auto-dispatch instead — fine on a dedicated dev project.)
"""

import os

from dotenv import load_dotenv

from livekit import agents
from livekit.agents import Agent, AgentSession
from livekit.plugins import atmee, deepgram, openai, silero

load_dotenv()

# The avatar to render: create one with examples/create_avatar.py.
ATMEE_AVATAR_ID = os.environ["ATMEE_AVATAR_ID"]


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions="You are a friendly assistant. Keep your answers short and spoken."
        )


async def entrypoint(ctx: agents.JobContext) -> None:
    await ctx.connect()

    session = AgentSession(
        stt=deepgram.STT(),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=openai.TTS(voice="alloy"),
        vad=silero.VAD.load(),
    )

    # ATMEE_API_KEY (and LIVEKIT_URL/API_KEY/API_SECRET) come from the environment.
    avatar = atmee.AvatarSession(avatar_id=ATMEE_AVATAR_ID)
    # Start the avatar before the agent session so the agent's audio is routed
    # to the avatar instead of being published directly.
    await avatar.start(session, room=ctx.room)

    await session.start(agent=Assistant(), room=ctx.room)
    await session.generate_reply(instructions="Greet the user and offer your help.")


if __name__ == "__main__":
    # agent_name set => explicit dispatch only: this worker joins a room only when
    # dispatched to it by name, never auto-joins every room in the project.
    agents.cli.run_app(
        agents.WorkerOptions(entrypoint_fnc=entrypoint, agent_name="atmee-cascade-demo")
    )
