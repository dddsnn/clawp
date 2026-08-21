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

from . import agent as agt
from . import api
from . import channel as chan
from . import config as cfg
from . import model as mdl
from . import provider as prov
from . import state as st

_log_fmt = "%(asctime)s|%(module)s|%(name)s|%(levelname)s: %(message)s"
_loggers_with_reduced_level = {
    "INFO": [
        "fabric",
        "httpcore",
        "invoke",
        "mcp.server.lowlevel.server",
        "nio",
        "paramiko.transport",
        "peewee",
        "urllib3.connectionpool",
    ],
    "WARNING": ["httpx"],
}
logging.config.dictConfig(
    {
        "version": 1,
        "formatters": {"simple": {"format": _log_fmt}},
        "handlers": {
            "stream_handler": {
                "class": "logging.StreamHandler",
                "formatter": "simple",
            }
        },
        "root": {"level": "DEBUG", "handlers": ["stream_handler"]},
        "loggers": {
            logger_name: {"level": level, "handlers": ["stream_handler"]}
            for level, loggers in _loggers_with_reduced_level.items()
            for logger_name in loggers
        },
    }
)
logger = logging.getLogger(__name__)


def shutdown(shutdown_event: asyncio.Event):
    shutdown_event.set()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="clawp", description="AI agent framework"
    )
    parser.add_argument(
        "-c", "--config-file", type=pathlib.Path, default="config.yaml"
    )
    return parser.parse_args()


async def main(config: mdl.GatewayConfig):
    shutdown_event = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(
        signal.SIGTERM, shutdown, shutdown_event
    )
    state_manager = st.GatewayStateManager(config.files_base_dir)
    openrouter_provider = prov.OpenrouterProvider(config.openrouter)
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(state_manager)
        channel_pool = chan.ChannelPool(config.channels, state_manager.state)
        await stack.enter_async_context(openrouter_provider)
        agent_repo = await stack.enter_async_context(
            agt.AgentRepository(
                base_dir=config.agents_base_dir,
                channel_pool=channel_pool,
                provider=openrouter_provider,
                config=config,
            )
        )
        await stack.enter_async_context(
            api.Api(config.api, agent_repo, channel_pool)
        )
        await shutdown_event.wait()


def run():
    args = parse_args()
    config = cfg.load_config(args.config_file)
    # Set the umask to configure which ermissions are set by default when
    # creating new files/directories.
    os.umask(config.gateway.umask)
    asyncio.run(main(config.gateway))


if __name__ == "__main__":
    run()
