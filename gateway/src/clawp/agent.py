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

import asyncio
import collections.abc as cl_abc
import functools as ft
import json
import logging
import pathlib
import typing as t
import uuid

import whenever as we

from . import channel as chan
from . import file, store, tool, util
from . import message as msg
from . import model as mdl

if t.TYPE_CHECKING:
    from . import provider as prov


class Session:
    """
    Session with an agent.

    The session essentially encapsulates the agent's context window. It can add
    messages to the context, generate agent responses using its provider, and
    also manages which tools the agent has available via the MCP client.

    The session is an asynchronous context manager that loads existing messages
    from the store on aenter and also ensures all agent messages have finished
    streaming when it shuts down.

    A session can only handle one request at a time (incoming message or
    requesting a response). Concurrent calls will block until the current one
    is done.
    """
    def __init__(
            self, session_seq: int, *, model_config: mdl.ModelConfig,
            message_store: store.SessionMessageStore,
            message_sender: chan.MessageSender, provider: "prov.Provider",
            mcp_client: tool.Client) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._session_seq = session_seq
        self._model_config = model_config
        self._message_store = message_store
        self._message_sender = message_sender
        self._provider = provider
        self._mcp_client = mcp_client
        self._messages = None
        self._lock = asyncio.Lock()
        self._is_shut_down = False
        self._publisher = util.Publisher()

    async def __aenter__(self) -> t.Self:
        self._messages = await self._message_store.read_session_messages()
        await self._publisher.__aenter__()
        return self

    async def __aexit__(self, *args) -> bool:
        async with self._lock:
            # Now that we've acquired the lock, we can be sure all message
            # streaming is done. Prevent any subsequent requests by setting the
            # shutdown flag.
            self._is_shut_down = True
        await self._publisher.__aexit__(*args)
        return False

    @property
    def num_messages(self) -> int:
        """The number of messages in this session."""
        return len(self._messages or [])

    async def add_incoming_message(
            self, incoming_message: chan.IncomingMessage) -> None:
        """
        Add an incoming message to this session.

        Metadata will be generated to put the incoming message into the context
        of the session and appended to the end of it.

        If the request_response flag is True, the provider is called to
        generate one or more AgentMessages in response to the current state of
        the session (with the new message added). Any tool calls the agent
        makes are handled. Only system and user messages are allowed to set the
        request_response flag. Both messages come with a reminder to the agent
        that they will respond on the same channel automatically. For user
        messages, this is part of the metadata message, for system messages, a
        small paragraph is appended to the message itself.

        The new message and any generated messages are are available via
        subscribe().
        """
        if (incoming_message.request_response
                and incoming_message.role not in ["system", "user"]):
            raise ValueError(
                "only system and user messages may request a response")
        async with self._lock:
            if self._is_shut_down:
                raise RuntimeError("shut down, can't process more messages")
            if incoming_message.role == "developer":
                message_class = msg.DeveloperMessage
            elif incoming_message.role == "system":
                message_class = msg.SystemMessage
            elif incoming_message.role == "user":
                message_class = msg.UserMessage
                await self._add_metadata_for_user_message(incoming_message)
            else:
                raise ValueError(
                    f"unable to handle message role {incoming_message.role}")
            metadata = self._make_metadata(
                incoming_message.metadata.time,
                incoming_message.metadata.channel)
            if incoming_message.request_response:
                try:
                    outgoing_channel = self._message_sender.response_channel(
                        incoming_message.metadata.channel)
                except chan.ChannelError:
                    self._logger.exception(
                        "Unable to find a response channel for incoming "
                        f"message with content '{incoming_message.content}' "
                        f"on channel {incoming_message.metadata.channel}. "
                        "Message will not be added or processed at all.")
                    return
            else:
                outgoing_channel = None
            message_content = incoming_message.content
            if (message_class is msg.SystemMessage
                    and incoming_message.request_response):
                # This is a system message prompting an agent response. Add a
                # reminder for the agent that its output will go on the system
                # channel.
                message_content += await file.render_message_template(
                    "fragments/channel_reminder.md")
            message = message_class(metadata, content=message_content)
            await self._append_message(message)
            if outgoing_channel:
                await self._request_responses(outgoing_channel)

    async def _add_metadata_for_user_message(
            self, user_message: chan.IncomingMessage):
        # The user message will be the one right after the system message with
        # the metadata, so seq_in_session should be one more.
        seq_in_session = len(self._messages) + 1
        metadata = await mdl.MessageMetadata.from_incoming_message(
            user_message.metadata, seq_in_session)
        message_content = await file.render_message_template(
            "message_metadata.md", metadata_json=metadata.model_dump_json())
        await self._append_message_now(
            msg.SystemMessage, content=message_content)

    async def _append_message_now(self, message_class, **kwargs):
        metadata = self._make_metadata(
            we.Instant.now(), mdl.SystemChannelDescriptor())
        message = message_class(metadata, **kwargs)
        await self._append_message(message)

    def _make_metadata(
        self,
        time: we.Instant | util.Value[we.Instant],
        channel: mdl.ChannelDescriptor,
    ) -> msg.MessageMetadata:
        if not isinstance(time, util.Value):
            time = util.ImmediateValue(time)
        return msg.MessageMetadata(
            seq_in_session=len(self._messages), time=time, channel=channel)

    async def _append_message(self, message):
        self._messages.append(message)
        # First, publish the message, so clients streaming it can get it before
        # it has fully arrived. Only then append it to the message store, which
        # requires the message to have finished streaming.
        await self._publisher.append(message)
        try:
            await self._message_store.append_message(message)
        except Exception:
            self._logger.exception(
                "Error storing message in persistent store. The message was "
                "added and is being processed in memory, but will likely not "
                "be present when reloading from the persistent store.")

    async def _request_responses(
            self, outgoing_channel: mdl.OutgoingChannelDescriptor) -> None:
        num_requests = 0
        while num_requests < self._model_config.doom_loop_max_requests:
            try:
                async with asyncio.timeout(
                        self._model_config.request_timeout.total("seconds")):
                    outgoing_channel, need_another_request = (
                        await self._request_response(outgoing_channel))
                    if not need_another_request:
                        break
            except TimeoutError:
                self._logger.error("Request timed out, giving up.")
                return
        else:
            self._logger.warning(
                f"Breaking out of request loop after {num_requests} requests.")

    async def _request_response(
            self, outgoing_channel: mdl.OutgoingChannelDescriptor):
        message, stream_task = await self._request_agent_message(
            outgoing_channel)
        send_task = asyncio.create_task(self._message_sender.send(message))
        # Wait for the message to completely arrive before handling tool calls.
        try:
            await stream_task
        except Exception:
            self._logger.exception(f"Error streaming {message}.")
        await message.wait_finalized()
        need_another_request = await self._handle_tool_calls(message)
        try:
            async with asyncio.timeout(
                    self._model_config.message_send_timeout.total("seconds")):
                await send_task
        except Exception as e:
            self._logger.exception(
                "Error sending message. Informing the agent to allow a retry.")
            await self._append_message_now(
                msg.SystemMessage, content=await
                file.render_message_send_error(message, e))
            # Set outgoing_channel so the agent's response goes to the system
            # channel.
            outgoing_channel = mdl.SystemChannelDescriptor()
            need_another_request = True
        return outgoing_channel, need_another_request

    async def _request_agent_message(
            self, outgoing_channel: mdl.OutgoingChannelDescriptor):
        parts = util.StreamableList()
        stream_task = await self._provider.stream_agent_message(
            parts, self._messages, self._mcp_client.tools.values())
        metadata = self._make_metadata(util.FutureValue(), outgoing_channel)
        message = msg.AgentMessage(metadata, parts)
        await self._append_message(message)
        return message, stream_task

    async def _handle_tool_calls(self, message: msg.AgentMessage) -> bool:
        if not await message.tool_calls:
            return False
        for tool_call in await message.tool_calls:
            self._logger.debug(f"Handling tool call {tool_call}.")
            try:
                arguments_dict = json.loads(tool_call.function.arguments)
                result_string = await self._mcp_client.call_tool(
                    tool_call.function.name, arguments_dict)
                await self._append_message_now(
                    msg.ToolMessage, content=result_string,
                    tool_call_id=tool_call.id)
            except Exception as e:
                await self._append_message_now(
                    msg.ToolMessage, content="Error in tool call: " + str(e),
                    tool_call_id=tool_call.id)
                self._logger.exception("Error in tool call.")
        return True

    async def add_agent_message(
            self, channel: mdl.OutgoingChannelDescriptor,
            content: str) -> msg.AgentMessage:
        """
        Add an agent message.

        Appends an agent message to this session with the given channel and
        content. Returns the agent message.

        This method must be called from a context where the session is already
        locked, i.e. from somewhere that gets called from
        add_incoming_message().
        """
        if not self._lock.locked():
            raise RuntimeError(
                "add_agent_message() must be called from a context where the "
                "session is locked")
        metadata = self._make_metadata(we.Instant.now(), channel)
        message = msg.AgentMessage.from_model(
            mdl.AgentMessage(
                metadata=await metadata.model, content=content, reasoning="",
                tool_calls=[], errors=[]))
        await self._append_message(message)
        return message

    def messages(self) -> cl_abc.Generator[msg.Message]:
        """Iterate all messages."""
        yield from self._messages

    def subscribe(self) -> cl_abc.AsyncGenerator[msg.Message]:
        """Subscribe to messages in this session."""
        return self._publisher.subscribe()


class Agent:
    """
    An agent.

    An agent manages a sequence of sessions that maintain the agent's
    personality and knowledge. It always has an active session, which
    represents the model's current context window. New sessions may be started
    (e.g. for compaction), but the agent should maintain their core memories
    and personality throughout.

    Since sessions are essentially append-only, when the history has to be
    changed for a compaction or change in system message, a new session is
    started.

    An agent is an asynchronous context manager that manages its MessageStore
    to persist messages in its session, manages its MCP client, and ensures
    sessions are properly opened and closed.
    """
    def __init__(
            self, agent_information: mdl.AgentInformation, *,
            model_config: mdl.ModelConfig, workspace_dir: pathlib.Path,
            message_store: store.MessageStore, memory_store: store.MemoryStore,
            channel_router: chan.ChannelRouter,
            provider: "prov.Provider") -> None:
        self._logger = logging.getLogger(type(self).__name__)
        if not workspace_dir.is_dir():
            raise ValueError("workspace doesn't exist")
        self._agent_information = agent_information
        self._workspace_dir = workspace_dir
        self._message_store = message_store
        self.memory_store = memory_store
        self._mcp_client = tool.Client(self)
        self._channel_router = channel_router
        self._session_factory = ft.partial(
            Session, model_config=model_config, message_sender=channel_router,
            provider=provider, mcp_client=self._mcp_client)
        self._session = None
        self._lock = asyncio.Lock()

    @property
    def information(self) -> mdl.AgentInformation:
        return self._agent_information

    @property
    def workspace_dir(self) -> pathlib.Path:
        return self._workspace_dir

    @property
    def channels(self) -> dict[str, chan.Channel]:
        """
        Return this agent's channels.

        The dictionary maps channel type to the channel.
        """
        return self._channel_router.channels

    @property
    def web_ui_channel(self) -> chan.WebUiChannel:
        """The agent's web UI channel."""
        return self._channel_router.web_ui_channel

    def __str__(self) -> str:
        return f"{type(self).__name__} {self.information.id}"

    async def __aenter__(self) -> t.Self:
        await self._message_store.__aenter__()
        await self._mcp_client.__aenter__()
        await self._channel_router.__aenter__()
        async with self._lock:
            self._read_incoming_messages_task = asyncio.create_task(
                self._read_incoming_messages())
            await self._ensure_active_session()
            return self

    async def __aexit__(self, *args) -> bool:
        async with self._lock:
            self._read_incoming_messages_task.cancel()
            try:
                async with asyncio.timeout(120):
                    await self._session.__aexit__(*args)
            except Exception:
                self._logger.exception("Error shutting down session.")
            try:
                async with asyncio.timeout(20):
                    await self._read_incoming_messages_task
            except Exception:
                self._logger.exception(
                    "Error waiting for incoming message task.")
        try:
            async with asyncio.timeout(5):
                await self._channel_router.__aexit__(*args)
        except Exception:
            self._logger.exception("Error closing channel router.")
        try:
            async with asyncio.timeout(10):
                await self._message_store.__aexit__(*args)
        except Exception:
            self._logger.exception("Error shutting down message store.")
        try:
            async with asyncio.timeout(10):
                await self._mcp_client.__aexit__(*args)
        except Exception:
            self._logger.exception("Error shutting down MCP client.")
        return False

    def _make_session(self, session_seq: int) -> Session:
        message_store = self._message_store.get_session_message_store(
            session_seq)
        return self._session_factory(session_seq, message_store=message_store)

    async def _ensure_active_session(self):
        active_session_seq = self._message_store.get_active_session_seq()
        self._session = self._make_session(active_session_seq)
        await self._session.__aenter__()
        if active_session_seq == 0 and not self._session.num_messages:
            self._logger.info(
                f"Existing agent {self} has no sessions. Starting the first "
                "one.")
            await self._send_session_init_messages()

    async def _start_new_session(self):
        if self._session:
            await self._session.__aexit__(None, None, None)
        self._session = self._make_session(
            self._message_store.get_active_session_seq() + 1)
        await self._session.__aenter__()
        await self._send_session_init_messages()

    async def _send_session_init_messages(self):
        async for message in self._onboarding_messages():
            await self._channel_router.system_channel.add_incoming_message(
                "developer", message)
        # Tell the agent that this is a new session.
        await self._channel_router.system_channel.add_incoming_message(
            "system", await file.render_message_template(
                "system_information/session_initialization.md"))
        # Tell the agent about available channels.
        for channel in self._channel_router.channels.values():
            await self._add_channel_status_message(channel)
        # Tell the agent about their workspace and personality files.
        await self._channel_router.system_channel.add_incoming_message(
            "system", await file.render_workspace_info(self))
        for pf in self.information.personality.personality_files:
            await self._channel_router.system_channel.add_incoming_message(
                "system", await
                file.render_file_content(self.workspace_dir, pf.path))

    async def _onboarding_messages(self) -> cl_abc.AsyncGenerator[str]:
        """
        Read all tutorials.

        Go through all tutorial messages in a sensible order for agent
        onboarding.
        """
        yield await file.render_message_template("init_system.md")
        tutorial_topics = [
            "system_sessions",
            "system_system_messages",
            "message_system_information",
            "message_message_metadata",
            "message_channel_status",
            "message_file_content",
            "system_channels",
            "channel_web_ui",
            "channel_system",
            "channel_matrix",
            "system_workspace_memory",]
        for topic in tutorial_topics:
            yield await file.render_tutorial(topic)

    async def _add_channel_status_message(self, channel: chan.Channel):
        await self._channel_router.system_channel.add_incoming_message(
            "system", await file.render_channel_status(
                channel, available=channel.type in self.channels))

    async def _read_incoming_messages(self) -> None:
        handle_task = None
        try:
            async for message in self._channel_router.incoming_messages():
                async with self._lock:
                    handle_task = asyncio.create_task(
                        self._handle_incoming_message(message))
                    await asyncio.shield(handle_task)
        except asyncio.CancelledError:
            if not handle_task:
                return
            try:
                async with asyncio.timeout(60):
                    await handle_task
            except Exception:
                self._logger.exception(
                    "Error waiting for final incoming message.")

    async def _handle_incoming_message(
            self, message: chan.IncomingMessage) -> None:
        try:
            await self._session.add_incoming_message(message)
        except Exception:
            self._logger.exception("Error handling incoming message.")

    def messages(self) -> cl_abc.Generator[msg.Message]:
        """
        Iterate all of this agent's messages.

        Yields all messages across all sessions that exist at the time of the
        call. To get live updates, use subscribe().
        """
        yield from self._session.messages()

    def subscribe(self) -> cl_abc.AsyncGenerator[msg.Message]:
        """
        Subscribe to the this agent's messages.

        These are all of the agent's messages in the context of its session and
        in the same order. This includes all message roles, also
        user/system/developer/tool messages.
        """
        return self._session.subscribe()

    async def add_and_send_agent_message(
            self, channel: mdl.OutgoingChannelDescriptor,
            content: str) -> None:
        """
        Sends a message on behalf of the agent.

        Adds an agent message with the given content to the current session and
        also sends it to the given channel.

        This method must be called from a context where the session is already
        locked, i.e. essentially from a tool callback that gets called from
        Session.add_incoming_message().

        Any error in sending is bubbled up.
        """
        message = await self._session.add_agent_message(channel, content)
        await self._channel_router.send(message)

    async def add_channel(self, channel: chan.Channel) -> None:
        """
        Add a channel to this agent.

        Raises a ValueError if a channel of this type already exists for the
        agent.
        """
        if channel.type in self.information.claimed_channels:
            raise ValueError(
                f"agent already has a channel of type {channel.type}")
        await self._channel_router.add_channel(channel)
        self.information.claimed_channels[channel.type] = channel.id
        await self._add_channel_status_message(channel)

    async def remove_channel(self, channel_type: mdl.ChannelType) -> None:
        """
        Remove a channel from this agent.

        Raises a ValueError if the agent currently has no channel of this type.
        """
        try:
            channel = self.channels[channel_type]
            assert channel_type in self.information.claimed_channels
        except (KeyError, AssertionError):
            raise ValueError(f"agent has no channel of type {channel_type}")
        await self._channel_router.remove_channel(channel_type)
        del self.information.claimed_channels[channel_type]
        await self._add_channel_status_message(channel)


class AgentRepository:
    """A repository of agents."""
    def __init__(
            self, *, base_dir: pathlib.Path, channel_pool: chan.ChannelPool,
            provider: "prov.Provider", model_config: mdl.ModelConfig) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._base_dir = base_dir
        self._channel_pool = channel_pool
        self._provider = provider
        self._model_config = model_config
        self._agents = {}
        self._running = False

    def iter_agents(self) -> cl_abc.Generator[Agent]:
        yield from self._agents.values()

    def get_agent(self, agent_id: uuid.UUID) -> Agent:
        return self._agents[agent_id]

    async def __aenter__(self) -> t.Self:
        if not self._base_dir.is_dir():
            self._logger.info(f"Creating agent repository {self._base_dir}.")
            self._base_dir.mkdir(parents=True, exist_ok=True)
        for agent in self._discover_agents():
            self._logger.info(f"Starting {agent}.")
            try:
                self._agents[agent.information.id] = await agent.__aenter__()
            except Exception:
                self._logger.exception(f"Error starting {agent}.")
                raise
        self._running = True
        return self

    async def __aexit__(self, *args) -> bool:
        self._running = False
        await self._stop_agents()
        for agent in self._agents.values():
            agent_base_dir = self._agent_base_dir(agent.information.id)
            self._agent_information_file(agent_base_dir).write_text(
                agent.information.model_dump_json())
        self._agents.clear()
        return False

    async def _stop_agents(self):
        stop_tasks = {
            asyncio.create_task(a.__aexit__(None, None, None))
            for a in self._agents.values()}
        if not stop_tasks:
            stop_tasks.add(asyncio.create_task(asyncio.sleep(0)))
        done, pending = await asyncio.wait(stop_tasks, timeout=120)
        for task in pending:
            self._logger.warning("Agent shutdown timed out.")
            task.cancel()
        for task in done:
            if task.exception():
                self._logger.error(
                    "Error shutting down agent.", exc_info=task.exception())

    def _agent_base_dir(self, agent_id: uuid.UUID) -> pathlib.Path:
        return self._base_dir / str(agent_id)

    def _discover_agents(self) -> cl_abc.Generator[Agent]:
        for d in self._base_dir.iterdir():
            if not d.is_dir():
                self._logger.warning(f"Ignoring unexpected non-directory {d}.")
                continue
            try:
                yield self._instantiate_agent(d)
            except ValueError:
                self._logger.exception(
                    f"Ignoring invalid agent directory {d}.")

    def _instantiate_agent(self, dir: pathlib.Path) -> Agent:
        agent_information = self._load_agent_information(dir)
        workspace_dir = self._workspace_dir(dir)
        if not self._workspace_dir(dir).is_dir():
            raise ValueError(f"missing workspace directory {workspace_dir}")
        message_store_dir = self._message_store_dir(dir)
        if not message_store_dir.is_dir():
            raise ValueError(f"missing message store {message_store_dir}")
        message_store = store.MessageStore(message_store_dir)
        memory_store_dir = self._memory_store_dir(dir)
        if not memory_store_dir.is_dir():
            raise ValueError(f"missing memory store {memory_store_dir}")
        memory_store = store.JsonlMemoryStore(memory_store_dir)
        channels = []
        for ch_type, ch_id in agent_information.claimed_channels.items():
            try:
                channel_status = self._channel_pool.acquire(ch_type, ch_id)
                channels.append(channel_status.channel)
            except chan.ChannelError as e:
                self._logger.warning(
                    f"Agent {agent_information.id} claims channel "
                    f"{ch_type}:{ch_id}, but it's not available: {e}.")
        return Agent(
            agent_information, model_config=self._model_config,
            workspace_dir=workspace_dir, message_store=message_store,
            memory_store=memory_store,
            channel_router=chan.ChannelRouter(channels),
            provider=self._provider)

    def _load_agent_information(
            self, agent_base_dir: pathlib.Path) -> mdl.AgentInformation:
        try:
            agent_id = uuid.UUID(agent_base_dir.name)
        except ValueError as e:
            raise ValueError("invalid agent ID in directory name") from e
        agent_information_file = self._agent_information_file(agent_base_dir)
        try:
            agent_information = mdl.AgentInformation.model_validate_json(
                agent_information_file.read_bytes())
        except Exception as e:
            raise ValueError("invalid agent information file") from e
        if agent_information.id != agent_id:
            raise ValueError(
                f"agent ID in information file ({agent_information.id}) "
                f"doesn't match the on in the directory name ({agent_id})")
        return agent_information

    def _agent_information_file(
            self, agent_base_dir: pathlib.Path) -> pathlib.Path:
        return agent_base_dir / "agent_information.json"

    def _workspace_dir(self, agent_base_dir: pathlib.Path) -> pathlib.Path:
        return agent_base_dir / "workspace"

    def _message_store_dir(self, agent_base_dir: pathlib.Path) -> pathlib.Path:
        return agent_base_dir / "message_store"

    def _memory_store_dir(self, agent_base_dir: pathlib.Path) -> pathlib.Path:
        return agent_base_dir / "memory_store"

    async def hatch_agent(self, personality_name: str) -> Agent:
        """Hatch a new agent."""
        if not self._running:
            raise RuntimeError("not running, can't hatch a new agent")
        agent_id = uuid.uuid4()
        agent_base_dir = await self._initialize_agent_files(
            agent_id, personality_name)
        agent = self._instantiate_agent(agent_base_dir)
        self._logger.info(f"Starting new {agent}.")
        try:
            self._agents[agent.information.id] = await agent.__aenter__()
        except Exception:
            self._logger.exception(f"Error starting new {agent}.")
            raise
        return self._agents[agent.information.id]

    async def _initialize_agent_files(self, agent_id, personality_name):
        try:
            personality_with_contents = (
                await
                file.read_personality_with_file_contents(personality_name))
        except file.PersonalityNotFoundError:
            raise
        except Exception as e:
            raise ValueError(
                f"can't use personality {personality_name}") from e
        self._logger.info(f"Setting up files for new agent {agent_id}.")
        agent_base_dir = self._base_dir / str(agent_id)
        agent_base_dir.mkdir(parents=True, exist_ok=True)
        agent_information = mdl.AgentInformation(
            id=agent_id,
            personality=personality_with_contents.get_personality())
        self._agent_information_file(agent_base_dir).write_text(
            agent_information.model_dump_json())
        self._logger.info(
            f"Created new agent information {agent_information}.")
        self._message_store_dir(agent_base_dir).mkdir(
            parents=True, exist_ok=True)
        self._memory_store_dir(agent_base_dir).mkdir(
            parents=True, exist_ok=True)
        workspace_dir = self._workspace_dir(agent_base_dir)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        for pf in personality_with_contents.personality_files:
            file_content = (
                personality_with_contents.personality_file_contents[pf.path])
            if file_content is None:
                # File shouldn't exist.
                continue
            (workspace_dir / pf.path).write_text(file_content)
        return agent_base_dir
