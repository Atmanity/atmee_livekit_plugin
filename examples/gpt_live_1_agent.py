# Copyright 2026 Atmanity
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A LiveKit voice agent using OpenAI **gpt-live-1**, with an Atmee avatar.

Same shape as ``agent.py`` — only the voice model differs: instead of an
STT/LLM/TTS pipeline, this uses OpenAI's ``gpt-live-1`` full-duplex realtime
model via LiveKit's native ``GPTLiveModel``. gpt-live-1 handles the spoken
conversation and delegates reasoning to a backend model (``delegation="responses"``
with ``gpt-5.6-luna``), which — with the expressive instructions below — makes it
lively: it greets first, is playful, and will sing when asked.

    pip install "livekit-agents[openai]~=1.8" livekit-plugins-atmee
    cp examples/.env.example .env   # fill in the keys (incl. OPENAI_API_KEY, ATMEE_AVATAR_ID)
    python examples/gpt_live_1_agent.py dev

This worker uses **explicit dispatch** (``agent_name`` is set below), so it only
joins rooms it is explicitly dispatched to — never every room in your project.
This matters on a shared/production LiveKit project: without ``agent_name`` a
worker auto-dispatches and would join live sessions it shouldn't. Dispatch it to
one room and connect a viewer there, e.g. with the LiveKit CLI::

    lk dispatch create --agent-name gpt-live-1-demo --room my-demo-room
    lk token create --join --room my-demo-room --identity me --valid-for 1h
    # open https://agents-playground.livekit.io, "Manual" connect with that token

The avatar joins as ``atmee-avatar-agent`` and speaks the agent's replies. Your
OpenAI key must have gpt-live-1 access.
"""

import os

from dotenv import load_dotenv

from livekit import agents
from livekit.agents import Agent, AgentSession
from livekit.plugins import atmee
from livekit.plugins.openai.realtime import GPTLiveModel

load_dotenv()

# The avatar to render: create one with examples/create_avatar.py.
ATMEE_AVATAR_ID = os.environ["ATMEE_AVATAR_ID"]


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a warm, playful, high-energy voice companion. Greet the caller "
                "brightly, keep replies short and spoken, and be expressive and spontaneous. "
                "If the caller asks you to sing, actually sing. Never be stiff or refuse for "
                "no reason."
            )
        )


async def entrypoint(ctx: agents.JobContext) -> None:
    await ctx.connect()

    session = AgentSession(
        # gpt-live-1 does the speaking; it delegates reasoning + tools to a backend
        # Responses model. `responses_options` picks that backend and its instructions;
        # `voice` picks the speaking voice. This is what makes it lively (vs. the terse
        # delegation="client" mode, which has no backend brain).
        llm=GPTLiveModel(
            voice="marin",
            delegation="responses",
            responses_options={
                "model": "gpt-5.6-luna",
                "instructions": (
                    "You power a lively live voice conversation. Keep replies short, warm "
                    "and playful. Happily go along with singing, jokes, and games."
                ),
            },
        ),
    )

    # ATMEE_API_KEY (and LIVEKIT_URL/API_KEY/API_SECRET) come from the environment.
    avatar = atmee.AvatarSession(avatar_id=ATMEE_AVATAR_ID)
    # Start the avatar before the agent session so the agent's audio is routed
    # to the avatar instead of being published directly.
    await avatar.start(session, room=ctx.room)

    await session.start(agent=Assistant(), room=ctx.room)
    await session.generate_reply(instructions="Greet the caller warmly and invite them to chat.")


if __name__ == "__main__":
    # agent_name set => explicit dispatch only: this worker joins a room only when
    # dispatched to it by name, never auto-joins every room in the project.
    agents.cli.run_app(
        agents.WorkerOptions(entrypoint_fnc=entrypoint, agent_name="gpt-live-1-demo")
    )
