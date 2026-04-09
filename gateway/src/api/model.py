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


class StartMessageMetadata(BaseModel):
    """Metadata available when a message is first created."""
    seq_in_session: t.Optional[int]


class EndMessageMetadata(BaseModel):
    """Metadata available when a message is fully received."""
    time: Iso8601Millis


class MessageMetadata(StartMessageMetadata, EndMessageMetadata):
    """Full message metadata."""


class BaseMessage(BaseModel):
    metadata: MessageMetadata
    role: t.Literal["assistant", "developer", "system", "tool", "user"]
    content: str


class DeveloperMessage(BaseMessage):
    """Message sent by a developer."""
    role: t.Literal["developer"] = "developer"


class SystemMessage(BaseMessage):
    """Message sent by the system."""
    role: t.Literal["system"] = "system"


class ToolMessage(BaseMessage):
    """Message sent by the system in response to a tool call."""
    role: t.Literal["tool"] = "tool"
    tool_call_id: str


class UserMessage(BaseMessage):
    """Message sent by the user."""
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
    """Message sent by the assistant."""
    role: t.Literal["assistant"] = "assistant"
    reasoning: str
    tool_calls: list[ToolCall]


NonStreamableMessage = (
    DeveloperMessage | SystemMessage | ToolMessage | UserMessage)

Message = AssistantMessage | NonStreamableMessage


class BaseStreamingMessageMarker(BaseModel):
    """A marker in the stream of a streamable message."""
    marker_type: t.Literal["message_start", "message_end", "part_start",
                           "part_end"]


class StreamingMessageMarkerMessageStart(BaseStreamingMessageMarker):
    """A marker signalling the start of the message."""
    marker_type: t.Literal["message_start"] = "message_start"
    metadata: StartMessageMetadata


class StreamingMessageMarkerMessageEnd(BaseStreamingMessageMarker):
    """A marker signalling the end of the message."""
    marker_type: t.Literal["message_end"] = "message_end"
    metadata: EndMessageMetadata


class StreamingMessageMarkerPartStart(BaseStreamingMessageMarker):
    """A marker signalling the start of a message part."""
    marker_type: t.Literal["part_start"] = "part_start"
    part_type: t.Literal["content", "error", "reasoning", "tool"]


class StreamingMessageMarkerPartEnd(BaseStreamingMessageMarker):
    """A marker signalling the end of a message part."""
    marker_type: t.Literal["part_end"] = "part_end"


StreamingMessageMarker = (
    StreamingMessageMarkerMessageStart | StreamingMessageMarkerMessageEnd
    | StreamingMessageMarkerPartStart | StreamingMessageMarkerPartEnd)


class BaseStreamingMessageFragment(BaseModel):
    """A fragment of a message part."""
    fragment_type: t.Literal["text", "tool_call"]
    fragment: str | ToolCall


class StreamingMessageFragmentText(BaseStreamingMessageFragment):
    """A fragment of a message part containing text."""
    fragment_type: t.Literal["text"] = "text"
    fragment: str


class StreamingMessageFragmentToolCall(BaseStreamingMessageFragment):
    """A fragment of a message part containing a tool call."""
    fragment_type: t.Literal["tool_call"] = "tool_call"
    fragment: ToolCall


StreamingMessageFragment = (
    StreamingMessageFragmentText | StreamingMessageFragmentToolCall)


class BaseWebsocketChunk(BaseModel):
    """A chunk of data sent in a websocket stream."""
    chunk_type: t.Literal["full_message", "assistant_message_marker",
                          "assistant_message_fragment"]
    payload: (
        NonStreamableMessage | StreamingMessageMarker
        | StreamingMessageFragment)


class WebsocketChunkFullMessage(BaseWebsocketChunk):
    """A chunk containing a full message."""
    chunk_type: t.Literal["full_message"] = "full_message"
    payload: NonStreamableMessage


class WebsocketChunkAssistantMessageMarker(BaseWebsocketChunk):
    """A chunk containing a marker in an streaming assistant message."""
    chunk_type: t.Literal["assistant_message_marker"] = (
        "assistant_message_marker")
    payload: StreamingMessageMarker


class WebsocketChunkAssistantMessageFragment(BaseWebsocketChunk):
    """A chunk containing a fragment in an streaming assistant message."""
    chunk_type: t.Literal["assistant_message_fragment"] = (
        "assistant_message_fragment")
    payload: StreamingMessageFragment


WebsocketChunk = (
    WebsocketChunkFullMessage | WebsocketChunkAssistantMessageMarker
    | WebsocketChunkAssistantMessageFragment)
