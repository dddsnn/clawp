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
import typing as t

import fastapi
import message as msg
import uvicorn

router = fastapi.APIRouter(prefix="/api/v1")


def get_consciousness_from_request(
        request: fastapi.Request) -> msg.Consciousness:
    try:
        consciousness = request.app.state.consciousness
        assert isinstance(consciousness, msg.Consciousness)
    except (AttributeError, AssertionError) as e:
        raise fastapi.HTTPException(
            status_code=500, detail="Consciousness is not available") from e
    return consciousness


def get_consciousness_from_websocket(
        websocket: fastapi.WebSocket) -> msg.Consciousness:
    try:
        consciousness = websocket.app.state.consciousness
        assert isinstance(consciousness, msg.Consciousness)
    except (AttributeError, AssertionError) as e:
        raise RuntimeError("Consciousness is not available") from e
    return consciousness


ConsciousnessDep = t.Annotated[msg.Consciousness,
                               fastapi.Depends(get_consciousness_from_request)]
ConsciousnessDepWs = t.Annotated[
    msg.Consciousness,
    fastapi.Depends(get_consciousness_from_websocket)]


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/stub")
async def stub_json(consciousness: ConsciousnessDep) -> dict[str, t.Any]:
    return {
        "message": "stub response",
        "version": "v1",}


@router.websocket("/ws")
async def websocket_endpoint(
        websocket: fastapi.WebSocket,
        consciousness: ConsciousnessDepWs) -> None:
    await websocket.accept()
    try:
        while True:
            payload = await websocket.receive_text()
            await websocket.send_json({"echo": payload})
    except fastapi.WebSocketDisconnect:
        return


class Api:
    def __init__(
            self, consciousness: msg.Consciousness, host: str = "127.0.0.1",
            port: int = 8000, log_level: str = "info") -> None:
        app = fastapi.FastAPI()
        app.state.consciousness = consciousness
        app.include_router(router)
        config = uvicorn.Config(
            app=app, host=host, port=port, log_level=log_level)
        self._server = uvicorn.Server(config)
        self._serve_task: t.Optional[asyncio.Task[None]] = None

    async def __aenter__(self) -> "Api":
        self._serve_task = asyncio.create_task(self._server.serve())

        while not self._server.started:
            if self._serve_task.done():
                await self._serve_task
                raise RuntimeError(
                    "API server exited before startup completed")
            await asyncio.sleep(0.05)

        return self

    async def __aexit__(self, *_) -> bool:
        self._server.should_exit = True
        with contextlib.suppress(asyncio.CancelledError):
            await self._serve_task
        return False
