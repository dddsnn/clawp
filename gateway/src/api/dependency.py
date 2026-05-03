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

import typing as t

import fastapi

import assistant as asst


def get_assistant_from_request(request: fastapi.Request) -> asst.Assistant:
    try:
        assistant = request.app.state.assistant
        assert isinstance(assistant, asst.Assistant)
    except (AttributeError, AssertionError) as e:
        raise fastapi.HTTPException(
            status_code=500, detail="Assistant is not available") from e
    return assistant


def get_assistant_from_websocket(
        websocket: fastapi.WebSocket) -> asst.Assistant:
    try:
        assistant = websocket.app.state.assistant
        assert isinstance(assistant, asst.Assistant)
    except (AttributeError, AssertionError) as e:
        raise RuntimeError("Assistant is not available") from e
    return assistant


Assistant = t.Annotated[asst.Assistant,
                        fastapi.Depends(get_assistant_from_request)]
AssistantWs = t.Annotated[asst.Assistant,
                          fastapi.Depends(get_assistant_from_websocket)]
