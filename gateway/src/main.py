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

import argparse
import asyncio
import contextlib
import logging
import logging.config
import os
import pathlib
import signal
import uuid

import agent as agt
import api
import channel as chan
import config as cfg
import provider as prov
import store
import tool

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
MATRIX_PASSWORD = os.environ["MATRIX_PASSWORD"]

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
            "httpcore": {"level": "INFO", "handlers": ["stream_handler"]},
            "nio": {"level": "INFO", "handlers": ["stream_handler"]},
            "peewee": {"level": "INFO", "handlers": ["stream_handler"]}},})


def shutdown(shutdown_event: asyncio.Event):
    shutdown_event.set()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="clawp", description="AI agent framework")
    parser.add_argument(
        "-c", "--config-file", type=pathlib.Path, default="config.yaml")
    return parser.parse_args()


async def main():
    shutdown_event = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(
        signal.SIGTERM, shutdown, shutdown_event)
    args = parse_args()
    config = cfg.load_config(args.config_file)
    message_store = store.MessageStore(config.gateway.message_store)
    matrix_channel = chan.MatrixChannel(config.gateway.matrix, MATRIX_PASSWORD)
    channel_repo = chan.ChannelRepository([
        chan.SystemChannel(),
        chan.WebUiChannel(), matrix_channel])
    openrouter_provider = prov.OpenrouterProvider(
        OPENROUTER_API_KEY, "stepfun/step-3.5-flash:free")
    mcp_client = tool.Client()
    agent_id = uuid.UUID(int=0)
    agent = agt.Agent(
        agent_id,
        message_store=message_store.get_agent_message_store(agent_id),
        channel_repo=channel_repo, provider=openrouter_provider,
        mcp_client=mcp_client)
    clawp_api = api.Api(agent, API_HOST, API_PORT, API_LOG_LEVEL)
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(message_store)
        await stack.enter_async_context(channel_repo)
        await stack.enter_async_context(openrouter_provider)
        await stack.enter_async_context(mcp_client)
        await stack.enter_async_context(agent)
        await stack.enter_async_context(clawp_api)
        await shutdown_event.wait()


if __name__ == "__main__":
    setup_logging()
    logger = logging.getLogger(__name__)
    asyncio.run(main())
