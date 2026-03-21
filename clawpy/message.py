# Copyright 2026 Marc Lehmann

# This file is part of clawpy.
#
# clawpy is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# clawpy is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
# details.
#
# You should have received a copy of the GNU Affero General Public License along
# with clawpy. If not, see <https://www.gnu.org/licenses/>.

import abc
import dataclasses as dc
import logging
import typing as t

import openrouter
import openrouter.components as or_comp

OpenRouterMessage = (
    or_comp.AssistantMessage | or_comp.SystemMessage
    | or_comp.ToolResponseMessage | or_comp.UserMessage)
MessageRole = t.Literal["assistant", "system", "tool", "user"]


class Message(abc.ABC):
    @property
    @abc.abstractmethod
    def role(self) -> MessageRole:
        return self._role

    @property
    @abc.abstractmethod
    def content(self) -> str:
        return self._content


class SimpleMessage(Message):
    def __init__(self, role: MessageRole, content: str) -> None:
        if role not in t.get_args(MessageRole):
            raise ValueError(f"Invalid role {role}.")
        self._role = role
        self._content = content

    @property
    def role(self) -> MessageRole:
        return self._role

    @property
    def content(self) -> str:
        return self._content


class SystemMessage(SimpleMessage):
    def __init__(self, content: str) -> None:
        super().__init__("system", content)


class UserMessage(SimpleMessage):
    def __init__(self, content: str) -> None:
        super().__init__("user", content)


class ToolMessage(SimpleMessage):
    def __init__(self, content: str, tool_call_id: str) -> None:
        super().__init__("system", content)
        self._tool_call_id = tool_call_id

    @property
    def tool_call_id(self) -> str:
        return self._tool_call_id


@dc.dataclass
class AssistantMessagePart:
    type: t.Literal["content", "reasoning"]
    content: str


class AssistantMessage(Message):
    def __init__(
            self, parts: list[AssistantMessagePart],
            tool_calls: list[or_comp.ChatStreamingMessageToolCall]) -> None:
        self._parts = parts
        self._tool_calls = tool_calls

    @property
    def role(self) -> t.Literal["assistant"]:
        return "assistant"

    @property
    def content(self) -> str:
        return "\n".join(
            part.content for part in self._parts if part.type == "content")

    @property
    def tool_calls(self) -> list[or_comp.ChatStreamingMessageToolCall]:
        return self._tool_calls

    @staticmethod
    async def from_event_stream(
            stream: or_comp.EventStreamAsync) -> AssistantMessage:
        parts, tool_calls = [], []
        current_content_type = None
        current_content = ""
        async for chunk in stream:
            if not isinstance(chunk, or_comp.ChatStreamingResponseChunk):
                raise ValueError(
                    f"Unexpected chunk type {type(chunk)} in stream.")
            if len(chunk.choices) != 1:
                raise ValueError(
                    f"Unexpected number of choices ({len(chunk.choices)}) in "
                    "chunk.")
            delta = chunk.choices[0].delta
            if delta.role != "assistant":
                raise ValueError(
                    f"Unexpected role {delta.role} in assistant message.")
            if delta.content and delta.reasoning:
                raise ValueError(
                    "Assistant message contains both content "
                    f"('{delta.content}') and reasoning ('{delta.reasoning}')."
                )
            if delta.tool_calls:
                for tool_call in delta.tool_calls:
                    if not tool_call.id:
                        # Chunks may contain null tool calls.
                        assert tool_call.type is None
                        continue
                    tool_calls.append(tool_call)
            if not delta.content and not delta.reasoning:
                continue
            content_type = "content" if delta.content else "reasoning"
            content = delta.content or delta.reasoning
            if current_content_type and current_content_type != content_type:
                parts.append(
                    AssistantMessagePart(
                        type=current_content_type, content=current_content))
                current_content = ""
            current_content_type = content_type
            current_content += content
        if current_content:
            parts.append(
                AssistantMessagePart(
                    type=current_content_type, content=current_content))
        return AssistantMessage(parts, tool_calls)


class Session:
    def __init__(self, openrouter_client: openrouter.OpenRouter) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._openrouter_client = openrouter_client
        self._messages = []
        self._tools = [
            or_comp.ToolDefinitionJSON(
                type="function", function=or_comp.ToolDefinitionJSONFunction(
                    name="ls", description="list current directory",
                    parameters={}, strict=True))]

    async def process_user_message(self,
                                   user_message_content: str) -> list[Message]:
        new_messages = []
        self._messages.append(UserMessage(user_message_content))
        while True:
            assistant_message = await AssistantMessage.from_event_stream(
                await self._request_stream())
            self._messages.append(assistant_message)
            new_messages.append(assistant_message)
            if not assistant_message.tool_calls:
                return new_messages
            for tool_call in assistant_message.tool_calls:
                self._logger.debug(f"Handling tool call {tool_call}.")
                assert tool_call.function.name == "ls"
                self._messages.append(
                    or_comp.ToolResponseMessage(
                        role="tool", tool_call_id=tool_call.id,
                        content="asd\nsdf"))

    async def _request_stream(self):
        self._logger.debug(f"sending {self._as_openrouter_message_list()}")
        return await self._openrouter_client.chat.send_async(
            messages=self._as_openrouter_message_list(),
            model="stepfun/step-3.5-flash:free", tools=self._tools,
            stream=True)

    def _as_openrouter_message_list(self) -> list[OpenRouterMessage]:
        openrouter_messages = []
        for message in self._messages:
            message_kwargs = {"role": message.role, "content": message.content}
            if message.role == "assistant":
                message_kwargs["tool_calls"] = message.tool_calls
                message_class = or_comp.AssistantMessage
            elif message.role == "system":
                message_class = or_comp.SystemMessage
            elif message.role == "tool":
                message_class = or_comp.ToolResponseMessage
                message_kwargs["tool_call_id"] = message.tool_call_id
            elif message.role == "user":
                message_class = or_comp.UserMessage
            else:
                raise ValueError(f"Invalid message role {message.role}.")
            openrouter_messages.append(message_class(**message_kwargs))
        return openrouter_messages
