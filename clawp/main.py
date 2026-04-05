# Copyright 2026 Marc Lehmann

# This file is part of clawp.
#
# clawp is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# clawp is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
# details.
#
# You should have received a copy of the GNU Affero General Public License along
# with clawp. If not, see <https://www.gnu.org/licenses/>.

import asyncio
import contextlib
import logging
import logging.config
import os
import signal
import sys

import api
import message as msg
import provider as prov
import tool

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]

API_HOST = "0.0.0.0"
API_PORT = 8000
API_LOG_LEVEL = "info"

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


async def do_chat(session: msg.Session):
    while True:
        try:
            await run_turn(session)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Error running chat turn.")


async def run_turn(session: msg.Session):
    user_message_content = await ainput()
    print("--- message sent, waiting for agent response ---")
    async for message in session.process_user_message(user_message_content):
        if not isinstance(message, msg.AssistantMessage):
            logger.warning(f"Got non-assistant message {message} as response.")
            if not await message.content:
                continue
            print(await message.content, flush=True)
        else:
            async for message_part in message.stream_parts():
                if isinstance(message_part, msg.AssistantMessageTextPart):
                    await print_text_part(message_part)
                    continue
                elif isinstance(message_part, msg.AssistantMessageErrorPart):
                    await print_error_part(message_part)
    print("--- end of agent message ---")


async def print_text_part(message_part: msg.AssistantMessageTextPart):
    print(f"{message_part.type}: ", end="")
    async for fragment in message_part.stream_fragments():
        print(fragment, end="", flush=True)
    print(flush=True)


async def print_error_part(message_part: msg.AssistantMessageErrorPart):
    async for exc in message_part.stream_fragments():
        msg = f"A {type(exc).__name__} occurred when receiving the message"
        if str(exc):
            msg += f": {exc}"
        msg += "."
        logger.error(msg)


async def main():
    shutdown_event = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(
        signal.SIGTERM, shutdown, shutdown_event)
    openrouter_provider = prov.OpenrouterProvider(
        OPENROUTER_API_KEY, "stepfun/step-3.5-flash:free")
    mcp_client = tool.Client()
    session = msg.Session(openrouter_provider, mcp_client)
    clawp_api = api.Api(session, API_HOST, API_PORT, API_LOG_LEVEL)
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(openrouter_provider)
        await stack.enter_async_context(mcp_client)
        await stack.enter_async_context(clawp_api)
        chat_task = asyncio.create_task(do_chat(session))
        await shutdown_event.wait()
        chat_task.cancel()
        await chat_task


if __name__ == "__main__":
    setup_logging()
    logger = logging.getLogger(__name__)
    asyncio.run(main())
