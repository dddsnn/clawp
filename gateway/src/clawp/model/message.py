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

import pydantic as pyd

from . import base
from . import channel as chan

InternalMessageRole = t.Literal["developer", "system", "tool"]
ChatMessageRole = t.Literal["agent", "user"]
MessageRole = InternalMessageRole | ChatMessageRole


class BasicStartMessageMetadata(base.BaseModel):
    """Metadata available when a message is first created."""

    chat: pyd.SerializeAsAny[chan.ChatDescriptor]


class GithubStartMessageMetadata(BasicStartMessageMetadata):
    chat: chan.GithubChatDescriptor
    comment_author_login: str
    comment_type: t.Literal["description", "comment"]


class MatrixStartMessageMetadata(BasicStartMessageMetadata):
    chat: chan.MatrixChatDescriptor
    sender_id: str
    sender_name: t.Optional[str]


StartMessageMetadata = (
    BasicStartMessageMetadata
    | GithubStartMessageMetadata
    | MatrixStartMessageMetadata
)


class EndMessageMetadata(base.BaseModel):
    """Metadata available when a message is fully received."""

    time: base.Iso8601Millis


class InternalMessageMetadata(EndMessageMetadata):
    """Full message metadata for internal messages."""


class BasicChatMessageMetadata(BasicStartMessageMetadata, EndMessageMetadata):
    """Full message metadata for chat messages."""

    start_metadata_class: t.ClassVar[type[BasicStartMessageMetadata]] = (
        BasicStartMessageMetadata
    )


class GithubChatMessageMetadata(
    GithubStartMessageMetadata, EndMessageMetadata
):
    start_metadata_class: t.ClassVar[type[GithubStartMessageMetadata]] = (
        GithubStartMessageMetadata
    )


class MatrixChatMessageMetadata(
    MatrixStartMessageMetadata, EndMessageMetadata
):
    start_metadata_class: t.ClassVar[type[MatrixStartMessageMetadata]] = (
        MatrixStartMessageMetadata
    )


ChatMessageMetadata = (
    BasicChatMessageMetadata
    | GithubChatMessageMetadata
    | MatrixChatMessageMetadata
)
MessageMetadata = InternalMessageMetadata | ChatMessageMetadata


class BaseMessage(base.BaseModel):
    role: MessageRole
    metadata: MessageMetadata
    content: str


class InternalMessage(BaseMessage):
    """Message that only exists internally."""

    role: InternalMessageRole
    metadata: InternalMessageMetadata


class ChatMessage(BaseMessage):
    """Message that arrives via channels/chats."""

    role: ChatMessageRole
    metadata: pyd.SerializeAsAny[ChatMessageMetadata]


class GithubChatMessage(ChatMessage):
    metadata: GithubChatMessageMetadata


class MatrixChatMessage(ChatMessage):
    metadata: MatrixChatMessageMetadata


class DeveloperMessage(InternalMessage):
    """Message sent by a developer."""

    role: t.Literal["developer"] = "developer"


class SystemMessage(InternalMessage):
    """Message sent by the system."""

    role: t.Literal["system"] = "system"


class ToolMessage(InternalMessage):
    """Message sent by the system in response to a tool call."""

    role: t.Literal["tool"] = "tool"
    tool_call_id: str


class UserMessage(ChatMessage):
    """Message sent by the user."""

    role: t.Literal["user"] = "user"


class ToolCallFunction(base.BaseModel):
    """A named function used in the agent's tool call."""

    name: str = ""
    arguments: str = ""


class ToolCall(base.BaseModel):
    """A tool call requested by the agent."""

    id: str
    function: ToolCallFunction


class AgentMessageError(base.BaseModel):
    """An error in an agent message."""

    type: str
    message: str
    kwargs: dict = pyd.Field(default_factory=dict)


class AgentMessage(ChatMessage):
    """Message sent by the agent."""

    role: t.Literal["agent"] = "agent"
    reasoning: str
    tool_calls: list[ToolCall]
    errors: list[AgentMessageError]


NonStreamableMessage = t.Annotated[
    DeveloperMessage | SystemMessage | ToolMessage | UserMessage,
    pyd.Field(discriminator="role"),
]

Message = t.Annotated[
    AgentMessage | NonStreamableMessage, pyd.Field(discriminator="role")
]
MessageTypeAdapter = pyd.TypeAdapter(Message)


class MessageOffset(base.BaseModel):
    session_seq: int
    message_seq: int


class MessageInSession(base.BaseModel):
    message: pyd.SerializeAsAny[Message]
    message_offset: MessageOffset


class IncomingMessage(base.BaseModel):
    """An unread message in a channel."""

    chat: chan.ChatDescriptor
    message: ChatMessage | SystemMessage


class BaseStreamingMessageMarker(base.BaseModel):
    """A marker in the stream of a streamable message."""

    marker_type: t.Literal[
        "message_start", "message_end", "part_start", "part_end"
    ]


class StreamingMessageMarkerMessageStart(BaseStreamingMessageMarker):
    """A marker signalling the start of the message."""

    marker_type: t.Literal["message_start"] = "message_start"
    metadata: StartMessageMetadata
    message_offset: MessageOffset


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
    StreamingMessageMarkerMessageStart
    | StreamingMessageMarkerMessageEnd
    | StreamingMessageMarkerPartStart
    | StreamingMessageMarkerPartEnd
)


class BaseStreamingMessageFragment(base.BaseModel):
    """A fragment of a message part."""

    fragment_type: t.Literal["text", "tool_call", "error"]
    fragment: str | ToolCall


class StreamingMessageFragmentText(BaseStreamingMessageFragment):
    """A fragment of a message part containing text."""

    fragment_type: t.Literal["text"] = "text"
    fragment: str


class StreamingMessageFragmentToolCall(BaseStreamingMessageFragment):
    """A fragment of a message part containing a tool call."""

    fragment_type: t.Literal["tool_call"] = "tool_call"
    fragment: ToolCall


class StreamingMessageFragmentError(BaseStreamingMessageFragment):
    """A fragment of a message part containing an error."""

    fragment_type: t.Literal["error"] = "error"
    fragment: AgentMessageError


StreamingMessageFragment = (
    StreamingMessageFragmentText
    | StreamingMessageFragmentToolCall
    | StreamingMessageFragmentError
)


class BaseWebsocketChunk(base.BaseModel):
    """A chunk of data sent in a websocket stream."""

    chunk_type: t.Literal[
        "full_message", "agent_message_marker", "agent_message_fragment"
    ]
    payload: (
        NonStreamableMessage
        | StreamingMessageMarker
        | StreamingMessageFragment
    )


class WebsocketChunkFullMessage(BaseWebsocketChunk):
    """A chunk containing a full message."""

    chunk_type: t.Literal["full_message"] = "full_message"
    payload: MessageInSession


class WebsocketChunkAgentMessageMarker(BaseWebsocketChunk):
    """A chunk containing a marker in an streaming agent message."""

    chunk_type: t.Literal["agent_message_marker"] = "agent_message_marker"
    payload: StreamingMessageMarker


class WebsocketChunkAgentMessageFragment(BaseWebsocketChunk):
    """A chunk containing a fragment in an streaming agent message."""

    chunk_type: t.Literal["agent_message_fragment"] = "agent_message_fragment"
    payload: StreamingMessageFragment


WebsocketChunk = (
    WebsocketChunkFullMessage
    | WebsocketChunkAgentMessageMarker
    | WebsocketChunkAgentMessageFragment
)


class BaseUserInputMessage(base.BaseModel):
    """A message sent from the user to the system."""

    type: t.Literal["message_content", "request_response"]


class UserInputMessageContent(BaseUserInputMessage):
    type: t.Literal["message_content"] = "message_content"
    content: str


class UserInputMessageRequestResponse(BaseUserInputMessage):
    type: t.Literal["request_response"] = "request_response"


UserInputMessage = t.Annotated[
    UserInputMessageContent | UserInputMessageRequestResponse,
    pyd.Field(discriminator="type"),
]
UserInputMessageTypeAdapter = pyd.TypeAdapter(UserInputMessage)
