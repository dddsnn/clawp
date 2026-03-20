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
import typing as t

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


@dc.dataclass
class AssistantMessagePart:
    type: t.Literal["content", "reasoning"]
    content: str


class AssistantMessage(Message):
    def __init__(self, parts: list[AssistantMessagePart]) -> None:
        self._parts = parts

    @property
    def role(self) -> t.Literal["assistant"]:
        return "assistant"

    @property
    def content(self) -> str:
        return "\n".join(
            part.content for part in self._parts if part.type == "content")

    @staticmethod
    async def from_event_stream(
            stream: or_comp.EventStreamAsync) -> AssistantMessage:
        parts = []
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
        return AssistantMessage(parts)


class Session:
    def __init__(self) -> None:
        self._messages = []

    def append(self, message: Message) -> None:
        self._messages.append(message)

    def as_openrouter_message_list(self) -> list[OpenRouterMessage]:
        openrouter_messages = []
        for message in self._messages:
            if message.role == "assistant":
                message_class = or_comp.AssistantMessage
            elif message.role == "system":
                message_class = or_comp.SystemMessage
            elif message.role == "tool":
                message_class = or_comp.ToolResponseMessage
            elif message.role == "user":
                message_class = or_comp.UserMessage
            else:
                raise ValueError(f"Invalid message role {message.role}.")
            openrouter_messages.append(
                message_class(role=message.role, content=message.content))
        return openrouter_messages
