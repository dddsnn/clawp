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
import typing as t

import fastapi
import uvicorn

from .. import agent as agt
from .. import channel as chan
from .. import model as mdl
from . import agent, channel, personality

logger = logging.getLogger(__name__)

router = fastapi.APIRouter(prefix="/api/v1")
router.include_router(agent.router)
router.include_router(channel.router)
router.include_router(personality.router)


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


class Api:
    def __init__(
        self,
        config: mdl.ApiConfig,
        agent_repo: agt.AgentRepository,
        channel_pool: chan.ChannelPool,
    ) -> None:
        app = fastapi.FastAPI()
        app.state.agent_repo = agent_repo
        app.state.channel_pool = channel_pool
        app.include_router(router)
        server_config = uvicorn.Config(
            app=app,
            host=str(config.host),
            port=config.port,
            log_level=config.log_level,
        )
        self._server = uvicorn.Server(server_config)
        self._serve_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> t.Self:
        self._serve_task = asyncio.create_task(self._server.serve())
        while not self._server.started:
            if self._serve_task.done():
                await self._serve_task
                raise RuntimeError(
                    "API server exited before startup completed"
                )
            await asyncio.sleep(0.05)

        return self

    async def __aexit__(self, *_) -> bool:
        assert self._serve_task is not None
        self._server.should_exit = True
        with contextlib.suppress(asyncio.CancelledError):
            await self._serve_task
        return False
