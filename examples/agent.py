"""A LiveKit voice agent with an Atmee avatar.

    pip install "livekit-agents[deepgram,openai,silero]" livekit-plugins-atmee
    cp examples/.env.example .env   # fill in the keys
    python examples/agent.py dev

Then open the LiveKit Agents Playground (https://agents-playground.livekit.io)
against your project: the avatar joins as `atmee-avatar-agent` and speaks the
agent's replies.
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
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
