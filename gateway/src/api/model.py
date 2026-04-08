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

import pydantic as pyd
import whenever as we

Iso8601Millis = t.Annotated[
    we.Instant,
    pyd.PlainSerializer(
        lambda i: i.format_iso(unit="millisecond"), return_type=str)]


class BaseModel(pyd.BaseModel):
    pass


class MessageMetadata(BaseModel):
    time: Iso8601Millis
    seq_in_session: t.Optional[int]


class BaseMessage(BaseModel):
    metadata: MessageMetadata
    role: t.Literal["assistant", "developer", "system", "tool", "user"]
    content: str


class DeveloperMessage(BaseMessage):
    role: t.Literal["developer"] = "developer"


class SystemMessage(BaseMessage):
    role: t.Literal["system"] = "system"


class ToolMessage(BaseMessage):
    role: t.Literal["tool"] = "tool"
    tool_call_id: str


class UserMessage(BaseMessage):
    role: t.Literal["user"] = "user"


class ToolCallFunction(BaseModel):
    """A named function used in the assistant's tool call."""
    name: str = ""
    arguments: str = ""


class ToolCall(BaseModel):
    """A tool call requested by the assistant."""
    id: str
    function: ToolCallFunction


class AssistantMessage(BaseMessage):
    role: t.Literal["assistant"] = "assistant"
    reasoning: str
    tool_calls: list[ToolCall]


Message = (
    AssistantMessage | DeveloperMessage | SystemMessage | ToolMessage
    | UserMessage)
