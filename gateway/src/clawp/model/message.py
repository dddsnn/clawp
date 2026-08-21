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


class BaseStartMessageMetadata[ChatDescriptorType](base.BaseModel):
    """Metadata available when a message is first created."""

    chat: pyd.SerializeAsAny[ChatDescriptorType]


class WebUiStartMessageMetadata(
    BaseStartMessageMetadata[chan.WebUiChatDescriptor]
):
    pass


class AgentStartMessageMetadata(
    BaseStartMessageMetadata[chan.AgentChatDescriptor]
):
    pass


class GithubStartMessageMetadata(
    BaseStartMessageMetadata[chan.GithubChatDescriptor]
):
    comment_author_login: str
    comment_type: t.Literal["description", "comment"]


class MatrixStartMessageMetadata(
    BaseStartMessageMetadata[chan.MatrixChatDescriptor]
):
    sender_id: str
    sender_name: str


StartMessageMetadata = (
    WebUiStartMessageMetadata
    | AgentStartMessageMetadata
    | GithubStartMessageMetadata
    | MatrixStartMessageMetadata
)


class EndMessageMetadata(base.BaseModel):
    """Metadata available when a message is fully received."""

    time: base.Iso8601Millis


class InternalMessageMetadata(EndMessageMetadata):
    """Full message metadata for internal messages."""


class WebUiChatMessageMetadata(WebUiStartMessageMetadata, EndMessageMetadata):
    start_metadata_class: t.ClassVar[type[WebUiStartMessageMetadata]] = (
        WebUiStartMessageMetadata
    )


class AgentChatMessageMetadata(AgentStartMessageMetadata, EndMessageMetadata):
    start_metadata_class: t.ClassVar[type[AgentStartMessageMetadata]] = (
        AgentStartMessageMetadata
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
    WebUiChatMessageMetadata
    | AgentChatMessageMetadata
    | GithubChatMessageMetadata
    | MatrixChatMessageMetadata
)
MessageMetadata = InternalMessageMetadata | ChatMessageMetadata


class BaseMessage[RoleType: MessageRole, MetadataType: MessageMetadata](
    base.BaseModel
):
    role: RoleType
    metadata: pyd.SerializeAsAny[MetadataType]
    content: str


class InternalMessage[RoleType: InternalMessageRole](
    BaseMessage[RoleType, InternalMessageMetadata]
):
    """Message that only exists internally."""


class BaseChatMessage[
    RoleType: ChatMessageRole,
    MetadataType: ChatMessageMetadata,
](BaseMessage[RoleType, MetadataType]):
    """Message that arrives via channels/chats."""


class GithubChatMessage(
    BaseChatMessage[ChatMessageRole, GithubChatMessageMetadata]
):
    pass


class MatrixChatMessage(
    BaseChatMessage[ChatMessageRole, MatrixChatMessageMetadata]
):
    pass


class DeveloperMessage(InternalMessage[t.Literal["developer"]]):
    """Message sent by a developer."""

    role: t.Literal["developer"] = "developer"


class SystemMessage(InternalMessage[t.Literal["system"]]):
    """Message sent by the system."""

    role: t.Literal["system"] = "system"


class ToolMessage(InternalMessage[t.Literal["tool"]]):
    """Message sent by the system in response to a tool call."""

    role: t.Literal["tool"] = "tool"
    tool_call_id: str


class UserMessage(BaseChatMessage[t.Literal["user"], ChatMessageMetadata]):
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
    kwargs: dict[str, t.Any] = pyd.Field(default_factory=dict)


class AgentMessage(BaseChatMessage[t.Literal["agent"], ChatMessageMetadata]):
    """Message sent by the agent."""

    role: t.Literal["agent"] = "agent"
    reasoning: str
    tool_calls: list[ToolCall]
    errors: list[AgentMessageError]


ChatMessage = (
    AgentMessage | GithubChatMessage | MatrixChatMessage | UserMessage
)
NonStreamableMessage = t.Annotated[
    DeveloperMessage | SystemMessage | ToolMessage | UserMessage,
    pyd.Field(discriminator="role"),
]

Message = t.Annotated[
    AgentMessage | NonStreamableMessage, pyd.Field(discriminator="role")
]


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


class BaseStreamingMessageMarker[MarkerType](base.BaseModel):
    """A marker in the stream of a streamable message."""

    marker_type: MarkerType


class StreamingMessageMarkerMessageStart(
    BaseStreamingMessageMarker[t.Literal["message_start"]]
):
    """A marker signalling the start of the message."""

    marker_type: t.Literal["message_start"] = "message_start"
    metadata: StartMessageMetadata
    message_offset: MessageOffset


class StreamingMessageMarkerMessageEnd(
    BaseStreamingMessageMarker[t.Literal["message_end"]]
):
    """A marker signalling the end of the message."""

    marker_type: t.Literal["message_end"] = "message_end"
    metadata: EndMessageMetadata


class StreamingMessageMarkerPartStart(
    BaseStreamingMessageMarker[t.Literal["part_start"]]
):
    """A marker signalling the start of a message part."""

    marker_type: t.Literal["part_start"] = "part_start"
    part_type: t.Literal["content", "error", "reasoning", "tool"]


class StreamingMessageMarkerPartEnd(
    BaseStreamingMessageMarker[t.Literal["part_end"]]
):
    """A marker signalling the end of a message part."""

    marker_type: t.Literal["part_end"] = "part_end"


StreamingMessageMarker = (
    StreamingMessageMarkerMessageStart
    | StreamingMessageMarkerMessageEnd
    | StreamingMessageMarkerPartStart
    | StreamingMessageMarkerPartEnd
)


class BaseStreamingMessageFragment[FragmentTypeLiteral, FragmentType](
    base.BaseModel
):
    """A fragment of a message part."""

    fragment_type: FragmentTypeLiteral
    fragment: FragmentType


class StreamingMessageFragmentText(
    BaseStreamingMessageFragment[t.Literal["text"], str]
):
    """A fragment of a message part containing text."""

    fragment_type: t.Literal["text"] = "text"


class StreamingMessageFragmentToolCall(
    BaseStreamingMessageFragment[t.Literal["tool_call"], ToolCall]
):
    """A fragment of a message part containing a tool call."""

    fragment_type: t.Literal["tool_call"] = "tool_call"


class StreamingMessageFragmentError(
    BaseStreamingMessageFragment[t.Literal["error"], AgentMessageError]
):
    """A fragment of a message part containing an error."""

    fragment_type: t.Literal["error"] = "error"


StreamingMessageFragment = (
    StreamingMessageFragmentText
    | StreamingMessageFragmentToolCall
    | StreamingMessageFragmentError
)


class BaseWebsocketChunk[ChunkType, PayloadType](base.BaseModel):
    """A chunk of data sent in a websocket stream."""

    chunk_type: ChunkType
    payload: PayloadType


class WebsocketChunkFullMessage(
    BaseWebsocketChunk[t.Literal["full_message"], MessageInSession]
):
    """A chunk containing a full message."""

    chunk_type: t.Literal["full_message"] = "full_message"


class WebsocketChunkAgentMessageMarker(
    BaseWebsocketChunk[
        t.Literal["agent_message_marker"], StreamingMessageMarker
    ]
):
    """A chunk containing a marker in an streaming agent message."""

    chunk_type: t.Literal["agent_message_marker"] = "agent_message_marker"


class WebsocketChunkAgentMessageFragment(
    BaseWebsocketChunk[
        t.Literal["agent_message_fragment"], StreamingMessageFragment
    ]
):
    """A chunk containing a fragment in an streaming agent message."""

    chunk_type: t.Literal["agent_message_fragment"] = "agent_message_fragment"


WebsocketChunk = (
    WebsocketChunkFullMessage
    | WebsocketChunkAgentMessageMarker
    | WebsocketChunkAgentMessageFragment
)


class BaseUserInputMessage[Type](base.BaseModel):
    """A message sent from the user to the system."""

    type: Type


class UserInputMessageContent(
    BaseUserInputMessage[t.Literal["message_content"]]
):
    type: t.Literal["message_content"] = "message_content"
    content: str


class UserInputMessageRequestResponse(
    BaseUserInputMessage[t.Literal["request_response"]]
):
    type: t.Literal["request_response"] = "request_response"


UserInputMessage = t.Annotated[
    UserInputMessageContent | UserInputMessageRequestResponse,
    pyd.Field(discriminator="type"),
]
UserInputMessageTypeAdapter = pyd.TypeAdapter(UserInputMessage)
