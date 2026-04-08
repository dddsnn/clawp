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

import typing as t

import fastapi

import message as msg


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


Consciousness = t.Annotated[msg.Consciousness,
                            fastapi.Depends(get_consciousness_from_request)]
ConsciousnessWs = t.Annotated[
    msg.Consciousness,
    fastapi.Depends(get_consciousness_from_websocket)]
