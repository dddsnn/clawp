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

import contextlib
from unittest.mock import AsyncMock, Mock

import mcp.types
import pytest
import whenever as we
from fastmcp.client.client import CallToolResult
from hamcrest import assert_that, contains_exactly, has_properties

from clawp import channel as chan
from clawp import file, store, tool, util
from clawp import message as msg
from clawp import model as mdl
from clawp import provider as prov
from clawp.agent.session import Session


class ScriptedProvider(prov.Provider):
    """Record finalized request contexts and stream one scripted reply each."""

    def __init__(self):
        self.replies = []
        self.requests = []
        self.offered_tools = []

    async def stream_agent_message(self, message_parts, messages, tools):
        self.requests.append([await message.model for message in messages])
        self.offered_tools.append(list(tools))
        reply = self.replies.pop(0)

        async def stream():
            for part in reply:
                await message_parts.append(part)
            await message_parts.finalize()

        return stream()


class RecordingSender(chan.MessageSender):
    def __init__(self):
        self.sent = []

    def make_outgoing_start_metadata(self, chat):
        assert isinstance(chat, mdl.WebUiChatDescriptor)
        return (
            mdl.WebUiStartMessageMetadata(chat=chat),
            mdl.WebUiChatMessageMetadata,
        )

    async def send(self, message):
        assert message.finalized()
        self.sent.append(await message.model)


class FakeToolClient:
    def __init__(self):
        self.tools = {
            "lookup": mcp.types.Tool(
                name="lookup", inputSchema={"type": "object"}
            )
        }
        self.calls = []
        self.error = None

    @contextlib.contextmanager
    def with_session_transaction(self, tx):
        assert not tx.is_complete()
        yield self

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.error:
            raise self.error
        return tool.ToolResult(
            raw_result=CallToolResult(
                content=[], structured_content=None, meta=None
            ),
            content_string="found it",
        )


def lookup_call():
    return msg.AgentMessageToolPart(
        [
            msg.ToolCall(
                id="lookup-1",
                function=msg.ToolCallFunction(
                    name="lookup", arguments='{"query": "clawp"}'
                ),
            )
        ]
    )


class TestSession:
    @pytest.fixture
    def provider(self):
        return ScriptedProvider()

    @pytest.fixture
    def sender(self):
        return RecordingSender()

    @pytest.fixture
    def tool_client(self):
        return FakeToolClient()

    @pytest.fixture
    async def sessions_store(self, tmp_path):
        async with store.SessionsStore(tmp_path / "sessions") as sessions:
            yield sessions

    @pytest.fixture
    async def session(self, sessions_store, provider, sender, tool_client):
        info_manager = Mock(spec=file.InfoManager)
        info_manager.missing_messages = AsyncMock(return_value=[])
        async with Session(
            0,
            model_config=mdl.ModelConfig(
                name="scripted",
                doom_loop_max_requests=5,
                message_send_timeout=we.TimeDelta(seconds=5),
                request_timeout=we.TimeDelta(seconds=10),
            ),
            sessions_store=sessions_store.for_session(0),
            message_sender=sender,
            info_manager=info_manager,
            provider=provider,
            mcp_client=tool_client,
            active_chat=mdl.WebUiChatDescriptor(chat_id=""),
        ) as session:
            yield session

    async def test_append_user_message_prepends_metadata_without_responding(
        self, session, sessions_store, provider, sender
    ):
        chat = mdl.WebUiChatDescriptor(chat_id="")
        metadata = mdl.WebUiChatMessageMetadata(
            chat=chat, time=we.Instant.from_utc(2026, 1, 1)
        )
        async with await session.transaction() as tx:
            await tx.append_incoming_message(
                mdl.IncomingMessage(
                    chat=chat,
                    message=mdl.UserMessage(
                        metadata=metadata, content="hello"
                    ),
                )
            )

        messages = list(session.messages())
        assert session.num_messages == 2
        assert_that(
            messages,
            contains_exactly(
                has_properties(
                    message=has_properties(role="system"),
                    message_offset=mdl.MessageOffset(
                        session_seq=0, message_seq=0
                    ),
                ),
                has_properties(
                    message=has_properties(role="user"),
                    message_offset=mdl.MessageOffset(
                        session_seq=0, message_seq=1
                    ),
                ),
            ),
        )
        assert metadata.model_dump_json() in await messages[0].message.content
        assert await messages[1].message.model == mdl.UserMessage(
            metadata=metadata, content="hello"
        )
        _, persisted = await sessions_store.load_or_create(0)
        assert [
            (await m.model).model_dump(mode="json") for m in persisted
        ] == [
            (await m.message.model).model_dump(mode="json") for m in messages
        ]
        assert provider.requests == []
        assert sender.sent == []

    async def test_append_system_message_does_not_add_chat_metadata(
        self, session, provider
    ):
        message = mdl.SystemMessage(
            metadata=mdl.InternalMessageMetadata(
                time=we.Instant.from_utc(2026, 1, 1)
            ),
            content="channel available",
        )
        async with await session.transaction() as tx:
            await tx.append_incoming_message(
                mdl.IncomingMessage(
                    chat=mdl.WebUiChatDescriptor(chat_id=""), message=message
                )
            )

        assert session.num_messages == 1
        assert await next(session.messages()).message.model == message
        assert provider.requests == []

    @pytest.mark.parametrize("content", ["hello", "", None])
    async def test_response_is_finalized_and_stored_but_only_content_is_sent(
        self, session, sessions_store, provider, sender, tool_client, content
    ):
        parts: list[msg.AgentMessagePart] = [
            msg.AgentMessageReasoningPart(["thinking"])
        ]
        if content is not None:
            parts.append(msg.AgentMessageContentPart([content]))
        provider.replies = [parts]
        async with await session.transaction() as tx:
            await tx.append_internal_message(msg.SystemMessage, "context")
            await tx.request_responses()

        assert len(provider.requests) == 1
        assert_that(
            provider.requests[0],
            contains_exactly(has_properties(role="system", content="context")),
        )
        assert provider.offered_tools == [list(tool_client.tools.values())]
        response = list(session.messages())[-1].message
        assert response.finalized()
        model = await response.model
        assert model.reasoning == "thinking"
        assert model.metadata.chat == mdl.WebUiChatDescriptor(chat_id="")
        assert model.content == (content or "")
        assert sender.sent == ([model] if content else [])
        _, persisted = await sessions_store.load_or_create(0)
        assert (await persisted[-1].model).model_dump(mode="json") == (
            model.model_dump(mode="json")
        )

    @pytest.mark.parametrize("fails", [False, True])
    async def test_tool_result_is_correlated_and_in_next_provider_context(
        self, session, provider, sender, tool_client, fails
    ):
        if fails:
            tool_client.error = ValueError("lookup unavailable")
        provider.replies = [
            [lookup_call()],
            [msg.AgentMessageContentPart(["answer"])],
        ]
        async with await session.transaction() as tx:
            await tx.request_responses()

        assert tool_client.calls == [("lookup", {"query": "clawp"})]
        assert len(provider.requests) == 2
        assert_that(
            provider.requests[1],
            contains_exactly(
                has_properties(
                    role="agent",
                    tool_calls=contains_exactly(has_properties(id="lookup-1")),
                ),
                has_properties(
                    role="tool",
                    tool_call_id="lookup-1",
                    content=(
                        "Error in tool call: lookup unavailable"
                        if fails
                        else "found it"
                    ),
                ),
            ),
        )
        assert [entry.message.role for entry in session.messages()] == [
            "agent",
            "tool",
            "agent",
        ]
        assert_that(
            sender.sent, contains_exactly(has_properties(content="answer"))
        )

    async def test_append_agent_message_handles_tools_without_continuation(
        self, session, provider, sender, tool_client
    ):
        async with await session.transaction() as tx:
            await tx.append_agent_message(
                util.StreamableList(
                    [msg.AgentMessageContentPart(["checking"]), lookup_call()]
                )
            )

        assert provider.requests == []
        assert tool_client.calls == [("lookup", {"query": "clawp"})]
        assert_that(
            sender.sent, contains_exactly(has_properties(content="checking"))
        )
        assert [entry.message.role for entry in session.messages()] == [
            "agent",
            "tool",
        ]
