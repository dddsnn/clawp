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
    return await asyncio.get_event_loop().run_in_executor(
        None, sys.stdin.readline)


async def do_chat(or_client: openrouter.OpenRouter):
    session = msg.Session()
    while True:
        try:
            await run_turn(or_client, session)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Error running chat turn.")


async def run_turn(or_client: openrouter.OpenRouter, session: msg.Session):
    user_message_content = await ainput()
    print("---")
    session.append(msg.UserMessage(user_message_content))
    event_stream = await or_client.chat.send_async(
        messages=session.as_openrouter_message_list(),
        model="stepfun/step-3.5-flash:free", stream=True)
    assistant_message = await msg.AssistantMessage.from_event_stream(
        event_stream)
    print(assistant_message.content)
    print("---")
    session.append(assistant_message)


async def main():
    shutdown_event = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(
        signal.SIGTERM, shutdown, shutdown_event)
    or_client = openrouter.OpenRouter(api_key=OPENROUTER_API_KEY)
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(or_client)
        chat_task = asyncio.create_task(do_chat(or_client))
        await shutdown_event.wait()
        chat_task.cancel()
        await chat_task


if __name__ == "__main__":
    setup_logging()
    logger = logging.getLogger(__name__)
    asyncio.run(main())
