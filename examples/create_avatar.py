"""Create an Atmee avatar from a single portrait.

    ATMEE_API_KEY=sk_atmee_... python examples/create_avatar.py portrait.jpg "Val"

Prints the avatar id to put into ATMEE_AVATAR_ID. A portrait-only avatar is
ready at once; nothing to wait for.
"""

import asyncio
import sys

from livekit.plugins import atmee


async def main(image: str, name: str) -> None:
    async with atmee.AtmeeAPI() as api:
        info = await api.create_avatar_info(name, image)
        print(f"avatar {info.avatar_id} ({info.kind}) is {info.status}")
        print(f"\nexport ATMEE_AVATAR_ID={info.avatar_id}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: create_avatar.py <portrait.jpg | https://...> [name]")
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "My avatar"))
