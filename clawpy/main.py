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
import logging
import logging.config
import os
import signal
import sys

import message as msg
import openrouter

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]

logger = None


def setup_logging():
    fmt = "%(asctime)s|%(module)s|%(name)s|%(levelname)s: %(message)s"
    logging.config.dictConfig({
        "version": 1,
        "formatters": {"simple": {"format": fmt}},
        "handlers": {
            "stream_handler": {
                "class": "logging.StreamHandler", "formatter": "simple"}},
        "root": {"level": "DEBUG", "handlers": ["stream_handler"]},
        "loggers": {
            "httpcore": {"level": "INFO", "handlers": ["stream_handler"]}},})


def shutdown(shutdown_event: asyncio.Event):
    shutdown_event.set()


async def ainput() -> str:
    return await asyncio.get_running_loop().run_in_executor(
        None, sys.stdin.readline)


async def do_chat(openrouter_client: openrouter.OpenRouter):
    session = msg.Session(openrouter_client)
    while True:
        try:
            await run_turn(session)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Error running chat turn.")


async def run_turn(session: msg.Session):
    user_message_content = await ainput()
    print("---")
    new_messages = await session.process_user_message(user_message_content)
    for message in new_messages:
        if not message.content:
            continue
        print(message.content)
        print("---")


async def main():
    shutdown_event = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(
        signal.SIGTERM, shutdown, shutdown_event)
    openrouter_client = openrouter.OpenRouter(api_key=OPENROUTER_API_KEY)
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(openrouter_client)
        chat_task = asyncio.create_task(do_chat(openrouter_client))
        await shutdown_event.wait()
        chat_task.cancel()
        await chat_task


if __name__ == "__main__":
    setup_logging()
    logger = logging.getLogger(__name__)
    asyncio.run(main())
