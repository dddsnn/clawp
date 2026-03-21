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
import json
import logging
import typing as t

import openrouter
import openrouter.components as or_comp
import tool

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
    text: str


@dc.dataclass
class ToolCallFunction:
    name: str = ""
    arguments: str = ""


@dc.dataclass
class ToolCall:
    id: str
    function: ToolCallFunction = dc.field(default_factory=ToolCallFunction)


class AssistantMessage(Message):
    def __init__(
            self, parts: list[AssistantMessagePart],
            tool_calls: list[ToolCall]) -> None:
        self._parts = parts
        self._tool_calls = tool_calls

    @property
    def role(self) -> t.Literal["assistant"]:
        return "assistant"

    @property
    def content(self) -> str:
        return "\n".join(
            part.text for part in self._parts if part.type == "content")

    @property
    def reasoning(self) -> str:
        return "\n".join(
            part.text for part in self._parts if part.type == "reasoning")

    @property
    def tool_calls(self) -> list[ToolCall]:
        return self._tool_calls

    @staticmethod
    async def from_event_stream(
            stream: or_comp.EventStreamAsync) -> AssistantMessage:
        parts, tool_calls, tool_calls_kwargs = [], [], {}
        current_part = None
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
            for tool_call in delta.tool_calls or []:
                tool_call_kwargs = tool_calls_kwargs.setdefault(
                    tool_call.index, {})
                tool_call_kwargs.setdefault("id", "")
                tool_call_kwargs.setdefault("name", "")
                tool_call_kwargs.setdefault("arguments", "")
                tool_call_kwargs["id"] += tool_call.id or ""
                tool_call_kwargs["name"] += tool_call.function.name or ""
                tool_call_kwargs["arguments"] += (
                    tool_call.function.arguments or "")
            if not delta.content and not delta.reasoning:
                continue
            this_type = "content" if delta.content else "reasoning"
            text = delta.content or delta.reasoning
            if current_part and current_part.type != this_type:
                parts.append(current_part)
                current_part = None
            current_part = current_part or AssistantMessagePart(
                type=this_type, text="")
            current_part.text += text
        if current_part:
            parts.append(current_part)
        for _, tool_call_kwargs in sorted(tool_calls_kwargs.items()):
            function = ToolCallFunction(
                name=tool_call_kwargs["name"],
                arguments=tool_call_kwargs["arguments"])
            tool_calls.append(
                ToolCall(id=tool_call_kwargs["id"], function=function))
        return AssistantMessage(parts, tool_calls)


class Session:
    def __init__(
            self, openrouter_client: openrouter.OpenRouter,
            mcp_client: tool.Client) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._openrouter_client = openrouter_client
        self._messages = []
        self._mcp_client = mcp_client

    @property
    def tools(self) -> list[or_comp.ToolDefinitionJSON]:
        return [
            or_comp.ToolDefinitionJSON(
                type="function", function=or_comp.ToolDefinitionJSONFunction(
                    name=t.name, description=t.description,
                    parameters=t.inputSchema, strict=True))
            for t in self._mcp_client.tools.values()]

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
                try:
                    arguments_dict = json.loads(tool_call.function.arguments)
                    result = await self._mcp_client.call_tool(
                        tool_call.function.name, arguments_dict)
                    self._messages.append(
                        or_comp.ToolResponseMessage(
                            role="tool", tool_call_id=tool_call.id,
                            content=str(result.data)))
                except Exception as e:
                    self._messages.append(
                        or_comp.ToolResponseMessage(
                            role="tool", tool_call_id=tool_call.id,
                            content="Error in tool call: " + str(e)))
                    self._logger.exception("Error in tool call.")

    async def _request_stream(self):
        return await self._openrouter_client.chat.send_async(
            messages=self._as_openrouter_message_list(),
            model="stepfun/step-3.5-flash:free", tools=self.tools, stream=True)

    def _as_openrouter_message_list(self) -> list[OpenRouterMessage]:
        openrouter_messages = []
        for message in self._messages:
            if message.role == "assistant":
                openrouter_message = self._create_openrouter_assistant_message(
                    message)
            elif message.role == "system":
                openrouter_message = or_comp.SystemMessage(
                    role=message.role, content=message.content)
            elif message.role == "tool":
                openrouter_message = or_comp.ToolResponseMessage(
                    role=message.role, content=message.content,
                    tool_call_id=message.tool_call_id)
            elif message.role == "user":
                openrouter_message = or_comp.UserMessage(
                    role=message.role, content=message.content)
            else:
                raise ValueError(f"Invalid message role {message.role}.")
            openrouter_messages.append(openrouter_message)
        return openrouter_messages

    @staticmethod
    def _create_openrouter_assistant_message(
            message: AssistantMessage) -> or_comp.AssistantMessage:
        tool_calls = []
        for tc in message.tool_calls:
            function = or_comp.ChatMessageToolCallFunction(
                name=tc.function.name, arguments=tc.function.arguments)
            tool_calls.append(
                or_comp.ChatMessageToolCall(
                    id=tc.id, type="function", function=function))
        return or_comp.AssistantMessage(
            role=message.role, content=message.content,
            reasoning=message.reasoning, tool_calls=tool_calls)
