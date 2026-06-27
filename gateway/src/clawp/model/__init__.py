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

from .agent import (
    AgentInformation,
    AgentPersonality,
    AgentPersonalityWithFileContents,
)
from .api import ErrorResponse
from .base import BaseModel, Iso8601Millis
from .channel import (
    AgentChannelStatus,
    ChannelInformation,
    ChannelStatus,
    ChannelType,
    ChatDescriptor,
    ChatInformation,
    MatrixChannelStatus,
    MatrixChatDescriptor,
    WebUiChannelStatus,
)
from .config import (
    ApiConfig,
    ChannelsConfig,
    Config,
    GatewayConfig,
    MatrixAccountConfig,
    MatrixConfig,
    ModelConfig,
    OpenRouterConfig,
    ShellConfig,
    ToolConfig,
)
from .memory import Memory
from .message import (
    AgentMessage,
    AgentMessageError,
    BaseMessage,
    BaseStreamingMessageFragment,
    BaseStreamingMessageMarker,
    BaseWebsocketChunk,
    ChatMessage,
    ChatMessageMetadata,
    ChatMessageRole,
    DeveloperMessage,
    EndMessageMetadata,
    InternalMessageMetadata,
    InternalMessageRole,
    Message,
    MessageInSession,
    MessageMetadata,
    MessageOffset,
    MessageRole,
    MessageTypeAdapter,
    NonStreamableMessage,
    StartMessageMetadata,
    StreamingMessageFragment,
    StreamingMessageFragmentError,
    StreamingMessageFragmentText,
    StreamingMessageFragmentToolCall,
    StreamingMessageMarker,
    StreamingMessageMarkerMessageEnd,
    StreamingMessageMarkerMessageStart,
    StreamingMessageMarkerPartEnd,
    StreamingMessageMarkerPartStart,
    SystemMessage,
    ToolCall,
    ToolCallFunction,
    ToolMessage,
    UserInputMessage,
    UserMessage,
    WebsocketChunk,
    WebsocketChunkAgentMessageFragment,
    WebsocketChunkAgentMessageMarker,
    WebsocketChunkFullMessage,
)
from .tool import ShellResult

__all__ = [
    # agent
    "AgentInformation",
    "AgentPersonality",
    "AgentPersonalityWithFileContents",
    # api
    "ErrorResponse",
    # base
    "BaseModel",
    "Iso8601Millis",
    # channel
    "AgentChannelStatus",
    "ChannelInformation",
    "ChannelStatus",
    "ChannelType",
    "ChatDescriptor",
    "ChatInformation",
    "MatrixChannelStatus",
    "MatrixChatDescriptor",
    "WebUiChannelStatus",
    # config
    "ApiConfig",
    "Config",
    "ChannelsConfig",
    "GatewayConfig",
    "MatrixAccountConfig",
    "MatrixConfig",
    "ModelConfig",
    "OpenRouterConfig",
    "ShellConfig",
    "ToolConfig",
    # memory
    "Memory",
    # message
    "AgentMessage",
    "AgentMessageError",
    "BaseMessage",
    "BaseStreamingMessageFragment",
    "BaseStreamingMessageMarker",
    "BaseWebsocketChunk",
    "ChatMessage",
    "ChatMessageMetadata",
    "ChatMessageRole",
    "DeveloperMessage",
    "EndMessageMetadata",
    "InternalMessageMetadata",
    "InternalMessageRole",
    "Message",
    "MessageInSession",
    "MessageMetadata",
    "MessageOffset",
    "MessageRole",
    "MessageTypeAdapter",
    "NonStreamableMessage",
    "StartMessageMetadata",
    "StreamingMessageFragment",
    "StreamingMessageFragmentError",
    "StreamingMessageFragmentText",
    "StreamingMessageFragmentToolCall",
    "StreamingMessageMarker",
    "StreamingMessageMarkerMessageEnd",
    "StreamingMessageMarkerMessageStart",
    "StreamingMessageMarkerPartEnd",
    "StreamingMessageMarkerPartStart",
    "SystemMessage",
    "ToolCall",
    "ToolCallFunction",
    "ToolMessage",
    "UserInputMessage",
    "UserMessage",
    "WebsocketChunk",
    "WebsocketChunkAgentMessageFragment",
    "WebsocketChunkAgentMessageMarker",
    "WebsocketChunkFullMessage",
    # tool
    "ShellResult",]
