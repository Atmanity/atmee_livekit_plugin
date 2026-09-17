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

"""Dispatch the gpt_live_1_agent worker to ONE room and open a viewer, viewer-first.

The example worker (``gpt_live_1_agent.py``) uses explicit dispatch
(``agent_name="gpt-live-1-demo"``), so it joins a room only when told to — it
never auto-joins every room in your LiveKit project. This helper does the telling:
it prints a viewer URL, waits for you to open it and join (so your mic is live),
THEN dispatches the avatar into that same room.

Viewer-first matters: dispatching into an *empty* room fails — gpt-live-1 has no
input audio to work from, and the room idle-tears-down before you arrive.

    # terminal 1 — the worker (idles until dispatched)
    python examples/gpt_live_1_agent.py dev

    # terminal 2 — this launcher (agent name defaults to gpt-live-1-demo)
    python examples/dispatch.py [agent-name]

Pass the worker's ``agent_name`` to dispatch a different example, e.g.
``python examples/dispatch.py atmee-cascade-demo`` for ``agent.py``.

Needs LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET in the environment (a
local .env works). The viewer URL points at the public LiveKit Meet sandbox; swap
VIEWER_APP for your own frontend if you have one.
"""

import asyncio
import os
import sys
import time

from dotenv import load_dotenv

from livekit import api

load_dotenv()

# Default matches WorkerOptions(agent_name=...) in gpt_live_1_agent.py; override
# with a command-line argument (e.g. "atmee-cascade-demo" for agent.py).
DEFAULT_AGENT_NAME = "gpt-live-1-demo"
VIEWER_APP = "https://meet.livekit.io/custom"


async def main() -> None:
    agent_name = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_AGENT_NAME
    url = os.environ["LIVEKIT_URL"]
    key = os.environ["LIVEKIT_API_KEY"]
    secret = os.environ["LIVEKIT_API_SECRET"]
    room = f"gl1-demo-{int(time.time())}"

    viewer = (
        api.AccessToken(key, secret)
        .with_identity("viewer")
        .with_name("You")
        .with_grants(api.VideoGrants(room_join=True, room=room))
        .to_jwt()
    )
    print(f"\nROOM: {room}")
    print(f"\nVIEWER_URL: {VIEWER_APP}?liveKitUrl={url}&token={viewer}\n")
    # input() blocks; keep it off the event loop.
    await asyncio.to_thread(
        input, "Open the URL above, allow your mic, then press Enter to send the avatar in... "
    )

    lk = api.LiveKitAPI(url=url, api_key=key, api_secret=secret)
    try:
        d = await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(agent_name=agent_name, room=room)
        )
        print(f"dispatched {d.id} ({agent_name}) -> {room}. The avatar joins shortly; talk to it.")
    finally:
        await lk.aclose()


if __name__ == "__main__":
    asyncio.run(main())
