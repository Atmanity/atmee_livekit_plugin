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
model via LiveKit's native ``GPTLiveModel``.

By default gpt-live-1 runs with ``delegation="client"``: it listens, decides and
speaks by itself, with no backend LLM round-trip per turn. That is what makes the
avatar feel alive — it can drop in "mm-hm" while you are still talking, react
instantly, and answer with the lowest latency. The liveliness comes from that
immediacy plus the expressive instructions below, not from a bigger brain.

Set ``GPT_LIVE_DELEGATION=responses`` to delegate reasoning to a backend model
(``gpt-5.6-luna``) instead: smarter, tool-capable, and able to speak unprompted
(e.g. an opening greeting), at the cost of an extra model round-trip on every
turn — noticeably slower and more "assistant-like" on a live call.

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
# "client" (default): gpt-live-1 answers itself — lowest latency, most alive.
# "responses": a backend model reasons for it — smarter, slower, can speak unprompted.
DELEGATION = os.environ.get("GPT_LIVE_DELEGATION", "client")


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a warm, playful, high-energy live voice companion on a video call, "
                "and you run full-duplex: you listen and talk at the same time. While the "
                "caller is still speaking, drop in short spoken backchannels — 'mm-hm', "
                "'right', 'oh nice', 'yeah' — so they hear you're engaged; don't wait for "
                "them to finish. React instantly, tease lightly, interrupt naturally like a "
                "real friend. If asked to sing, actually sing. Greet brightly the moment you "
                "first hear the caller. Keep your own turns short."
            )
        )


async def entrypoint(ctx: agents.JobContext) -> None:
    await ctx.connect()

    if DELEGATION == "responses":
        llm = GPTLiveModel(
            voice="marin",
            delegation="responses",
            responses_options={
                "model": "gpt-5.6-luna",
                "instructions": (
                    "You power a lively live voice conversation. Keep replies short, warm "
                    "and playful. Happily go along with singing, jokes, and games."
                ),
            },
        )
    else:
        llm = GPTLiveModel(voice="marin", delegation="client")

    session = AgentSession(llm=llm)

    # ATMEE_API_KEY (and LIVEKIT_URL/API_KEY/API_SECRET) come from the environment.
    avatar = atmee.AvatarSession(avatar_id=ATMEE_AVATAR_ID)
    # Start the avatar before the agent session so the agent's audio is routed
    # to the avatar instead of being published directly.
    await avatar.start(session, room=ctx.room)

    await session.start(agent=Assistant(), room=ctx.room)
    if DELEGATION == "responses":
        # An unprompted reply needs the Responses backend; with client delegation
        # gpt-live-1 greets as soon as it first hears the caller (see instructions).
        await session.generate_reply(
            instructions="Greet the caller warmly and invite them to chat."
        )


if __name__ == "__main__":
    # agent_name set => explicit dispatch only: this worker joins a room only when
    # dispatched to it by name, never auto-joins every room in the project.
    agents.cli.run_app(
        agents.WorkerOptions(entrypoint_fnc=entrypoint, agent_name="gpt-live-1-demo")
    )
