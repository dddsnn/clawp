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
import pathlib
import signal

from . import agent as agt
from . import api
from . import channel as chan
from . import config as cfg
from . import model as mdl
from . import provider as prov

_log_fmt = "%(asctime)s|%(module)s|%(name)s|%(levelname)s: %(message)s"
logging.config.dictConfig({
    "version": 1,
    "formatters": {"simple": {"format": _log_fmt}},
    "handlers": {
        "stream_handler": {
            "class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"level": "DEBUG", "handlers": ["stream_handler"]},
    "loggers": {
        "httpcore": {"level": "INFO", "handlers": ["stream_handler"]},
        "nio": {"level": "INFO", "handlers": ["stream_handler"]},
        "peewee": {"level": "INFO", "handlers": ["stream_handler"]}},})
logger = logging.getLogger(__name__)


def shutdown(shutdown_event: asyncio.Event):
    shutdown_event.set()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="clawp", description="AI agent framework")
    parser.add_argument(
        "-c", "--config-file", type=pathlib.Path, default="config.yaml")
    return parser.parse_args()


def make_channels(config: mdl.GatewayConfig) -> dict[str, chan.Channel]:
    channels = {"system": chan.SystemChannel(), "web_ui": chan.WebUiChannel()}
    if config.matrix:
        channels["matrix"] = chan.MatrixChannel(config.matrix)
    return channels


async def main():
    shutdown_event = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(
        signal.SIGTERM, shutdown, shutdown_event)
    args = parse_args()
    config = cfg.load_config(args.config_file)
    channels = make_channels(config.gateway)
    channel_repo = chan.ChannelRepository(channels.values())
    openrouter_provider = prov.OpenrouterProvider(config.gateway.openrouter)
    agent_repo = agt.AgentRepository(
        base_dir=config.gateway.agents_base_dir, channel_repo=channel_repo,
        provider=openrouter_provider)
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(channel_repo)
        await stack.enter_async_context(openrouter_provider)
        await stack.enter_async_context(agent_repo)
        agent_ids = agent_repo.list_agents()
        assert len(agent_ids) == 1
        agent = agent_repo.get_agent(next(iter(agent_ids)))
        await stack.enter_async_context(api.Api(config.gateway.api, agent))
        await shutdown_event.wait()


def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()
