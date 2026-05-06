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
# You should have received a copy of the GNU Affero General Public License
# along with clawp. If not, see <https://www.gnu.org/licenses/>.

import asyncio
import contextlib
import logging
import logging.config
import os
import pathlib
import signal
import uuid

import api
import assistant as asst
import provider as prov
import store
import tool

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
MESSAGE_STORE_PATH = pathlib.Path(os.environ["MESSAGE_STORE_PATH"])

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


async def main():
    shutdown_event = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(
        signal.SIGTERM, shutdown, shutdown_event)
    message_store = store.MessageStore(MESSAGE_STORE_PATH)
    openrouter_provider = prov.OpenrouterProvider(
        OPENROUTER_API_KEY, "stepfun/step-3.5-flash:free")
    mcp_client = tool.Client()
    agent_id = uuid.UUID(int=0)
    agent = asst.Agent(
        agent_id,
        message_store=message_store.get_agent_message_store(agent_id),
        provider=openrouter_provider, mcp_client=mcp_client)
    clawp_api = api.Api(agent, API_HOST, API_PORT, API_LOG_LEVEL)
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(message_store)
        await stack.enter_async_context(openrouter_provider)
        await stack.enter_async_context(mcp_client)
        await stack.enter_async_context(agent)
        await stack.enter_async_context(clawp_api)
        await shutdown_event.wait()


if __name__ == "__main__":
    setup_logging()
    logger = logging.getLogger(__name__)
    asyncio.run(main())
