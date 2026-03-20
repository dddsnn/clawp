# Copyright 2026 Marc Lehmann

# This file is part of clawpy.
#
# clawpy is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# clawpy is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
# details.
#
# You should have received a copy of the GNU Affero General Public License along
# with clawpy. If not, see <https://www.gnu.org/licenses/>.

import asyncio
import contextlib
import os
import signal

import message as msg
import openrouter

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]


def shutdown(shutdown_event: asyncio.Event):
    shutdown_event.set()


async def main():
    shutdown_event = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(
        signal.SIGTERM, shutdown, shutdown_event)
    or_client = openrouter.OpenRouter(api_key=OPENROUTER_API_KEY)
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(or_client)
        await shutdown_event.wait()


if __name__ == "__main__":
    asyncio.run(main())
