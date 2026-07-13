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
import uuid

import whenever as we

from .. import message as msg
from .. import model as mdl
from .. import store
from . import base

if t.TYPE_CHECKING:
    from .. import agent as agt


class WebUiChannel(base.Channel):
    """
    Web UI channel.

    This channel is used for the built-in web UI.

    This channel persists all messages (incoming and outgoing) in a .jsonl
    file. It also maintains a read offset (the index of the next unread
    message) in a config object that must be persisted externally.
    """
    _MESSAGES_FILE_NAME = "messages.jsonl"
    _MESSAGES_VERSION = 0

    def __init__(self, persistence_info: mdl.WebUiChannelPersistence) -> None:
        """
        :param persistence_info: A config object for the persistence. This
            also contains the read offset, which is updated inside that
            instance and must be persisted externally.
        """
        super().__init__("web_ui")
        self._persistence_info = persistence_info
        self._messages: list[mdl.ChatMessage] = []
        self._messages_io = store.JsonlIO(
            self._persistence_info.messages_dir / self._MESSAGES_FILE_NAME,
            mdl.MessageTypeAdapter)

    async def __aenter__(self) -> t.Self:
        await super().__aenter__()
        await self._load_messages_from_disk()
        if not 0 <= self._read_offset <= len(self._messages):
            self._logger.error(
                f"Read offset on disk is invalid (was {self._read_offset}, "
                f"with {len(self._messages)} messages. Resetting to end.")
            self._read_offset = len(self._messages)
        return self

    async def __aexit__(self, *args) -> bool:
        await self._messages_io.close()
        return await super().__aexit__(*args)

    @property
    def _read_offset(self) -> int:
        return self._persistence_info.read_offset

    @_read_offset.setter
    def _read_offset(self, value: int) -> None:
        self._persistence_info.read_offset = value

    async def _load_messages_from_disk(self) -> None:
        try:
            models = [model async for model in self._messages_io.read_all()]
        except FileNotFoundError:
            models = []
        messages: list[mdl.ChatMessage] = []
        for model in models:
            if not isinstance(model, mdl.ChatMessage):
                raise base.ChannelError(
                    "web_ui channel file contains "
                    f"non-chat message {type(model)}")
            if (model.metadata.chat.channel != "web_ui"
                    or model.metadata.chat.chat_id != ""):
                raise base.ChannelError(
                    "web_ui channel file contains message for wrong chat")
            messages.append(model)
        self._messages = messages

    async def _append_message(self, message: mdl.ChatMessage) -> None:
        self._messages.append(message)
        try:
            await self._messages_io.append(message)
        except FileNotFoundError:
            await self._messages_io.create({
                "version": self._MESSAGES_VERSION,
                "channel": "web_ui",})
            await self._messages_io.append(message)

    @property
    def id(self) -> None:
        return None

    @property
    async def status(self) -> mdl.WebUiChannelStatus:
        return mdl.WebUiChannelStatus(available=True)

    async def get_chat_descriptor(
            self, chat_id: str) -> mdl.BasicChatDescriptor:
        if chat_id != "":
            raise base.ChatIdError("invalid chat_id (use empty string \"\")")
        return mdl.BasicChatDescriptor(channel=self.type, chat_id=chat_id)

    async def get_unread_messages(self, chat_id: str) -> list[mdl.ChatMessage]:
        if chat_id != "":
            raise base.ChatIdError("invalid chat_id (use empty string \"\")")
        unread_messages = self._messages[self._read_offset:]
        self._read_offset = len(self._messages)
        return unread_messages

    def make_outgoing_start_metadata(
        self, chat: mdl.ChatDescriptor
    ) -> tuple[mdl.BasicStartMessageMetadata,
               type[mdl.BasicChatMessageMetadata]]:
        if chat.channel != "web_ui":
            raise ValueError(f"got descriptor for {chat.channel}")
        return (
            mdl.BasicStartMessageMetadata(chat=chat),
            mdl.BasicChatMessageMetadata)

    async def send(self, message: msg.AgentMessage) -> None:
        chat_message = await message.model
        if len(self._messages) != self._read_offset:
            raise base.ChannelError("can't send if there are unread messages")
        await self._append_message(chat_message)
        self._read_offset = len(self._messages)

    async def add_incoming_user_message(
            self, time: we.Instant, content: str) -> None:
        """
        Add a user message.

        The message will appear has having arrived on the channel and will be
        delivered to the agent.
        """
        metadata = mdl.BasicChatMessageMetadata(
            time=time,
            chat=mdl.BasicChatDescriptor(channel=self.type, chat_id=""))
        message = mdl.ChatMessage(
            role="user", metadata=metadata, content=content)
        await self._append_message(message)
        await self._publisher.append(message)


class AgentChannel(base.Channel):
    """
    Agent channel.

    This channel can be used by Clawp agents to directly communicate with other
    agents within the system.

    This channel persists all messages (incoming and outgoing) in .jsonl files
    named after the other agents in the chats (i.e. the file name is the other
    agent's ID). It also maintains read offsets (the index of the next unread
    message) for each chat in a config object that must be persisted
    externally.
    """
    _MESSAGES_VERSION = 0

    def __init__(
            self, agent_id: uuid.UUID, agent_repo: "agt.AgentRepository",
            persistence_info: mdl.AgentChannelPersistence) -> None:
        """
        :param persistence_info: A config object for the persistence. This
            also contains the read offsets, which are updated inside that
            instance and must be persisted externally.
        """
        super().__init__("agent")
        self._agent_id = agent_id
        self._agent_repo = agent_repo
        self._persistence_info = persistence_info
        self._messages: dict[uuid.UUID, list[mdl.ChatMessage]] = {}
        self._chat_ios: dict[uuid.UUID, store.JsonlIO] = {}

    async def __aenter__(self) -> t.Self:
        await super().__aenter__()
        await self._load_messages_from_disk()
        return self

    async def __aexit__(self, *args) -> bool:
        for io in self._chat_ios.values():
            await io.close()
        self._chat_ios.clear()
        return await super().__aexit__(*args)

    def _messages_path(self, peer_agent_id: uuid.UUID):
        return self._persistence_info.messages_dir / f"{peer_agent_id}.jsonl"

    def _io_for_chat(self, peer_agent_id: uuid.UUID) -> store.JsonlIO:
        try:
            return self._chat_ios[peer_agent_id]
        except KeyError:
            io = store.JsonlIO(
                self._messages_path(peer_agent_id), mdl.MessageTypeAdapter)
            self._chat_ios[peer_agent_id] = io
            return io

    async def _load_messages_from_disk(self) -> None:
        messages_dir = self._persistence_info.messages_dir
        if not messages_dir.exists():
            return
        for path in messages_dir.iterdir():
            if not path.is_file() or path.suffix != ".jsonl":
                continue
            try:
                peer_agent_id = uuid.UUID(path.stem)
            except ValueError:
                self._logger.warning(
                    f"Ignoring unexpected agent-channel file {path}.")
                continue
            io = self._io_for_chat(peer_agent_id)
            models = [model async for model in io.read_all()]
            chat_messages: list[mdl.ChatMessage] = []
            for model in models:
                if not isinstance(model, mdl.ChatMessage):
                    raise base.ChannelError(
                        "agent channel file contains "
                        f"non-chat message {type(model)}")
                if (model.metadata.chat.channel != "agent"
                        or model.metadata.chat.chat_id != str(peer_agent_id)):
                    raise base.ChannelError(
                        "agent channel file contains message for wrong chat")
                chat_messages.append(model)
            self._messages[peer_agent_id] = chat_messages

    def _get_read_offset(
            self, peer_agent_id: uuid.UUID, *, default: int) -> int:
        try:
            return self._persistence_info.read_offsets[peer_agent_id]
        except KeyError:
            return default

    def _set_read_offset(
            self, peer_agent_id: uuid.UUID, read_offset: int) -> None:
        messages = self._messages.setdefault(peer_agent_id, [])
        if not 0 <= read_offset <= len(messages):
            raise ValueError("new offset is not in valid range")
        self._persistence_info.read_offsets[peer_agent_id] = read_offset

    async def _append_message(
            self, peer_agent_id: uuid.UUID, message: mdl.ChatMessage) -> None:
        self._messages.setdefault(peer_agent_id, []).append(message)
        io = self._io_for_chat(peer_agent_id)
        try:
            await io.append(message)
        except FileNotFoundError:
            await io.create({
                "version": self._MESSAGES_VERSION,
                "channel": "agent",
                "peer_agent_id": str(peer_agent_id),})
            await io.append(message)

    @property
    def id(self) -> str:
        return str(self._agent_id)

    @property
    async def status(self) -> mdl.AgentChannelStatus:
        return mdl.AgentChannelStatus(available=True)

    async def get_chat_descriptor(self, chat_id: str) -> mdl.ChatDescriptor:
        # Get the other agent to make sure the ID is in order and the agent
        # exists.
        self._get_agent(chat_id)
        return mdl.BasicChatDescriptor(channel=self.type, chat_id=chat_id)

    def _get_agent(self, chat_id: str) -> "agt.Agent":
        try:
            agent_id = uuid.UUID(chat_id)
        except ValueError:
            raise base.ChatIdError(f"{chat_id} is not a valid UUID")
        if agent_id == self._agent_id:
            raise base.ChatIdError("sender and recipient IDs are equal")
        try:
            return self._agent_repo.get_agent(agent_id)
        except KeyError:
            raise base.ChatIdError(f"no agent with ID {agent_id} exists")

    async def get_unread_messages(self, chat_id: str) -> list[mdl.ChatMessage]:
        # Get the other agent to make sure the ID is in order and the agent
        # exists.
        peer_agent = self._get_agent(chat_id)
        messages = self._messages.setdefault(peer_agent.state.id, [])
        read_offset = self._get_read_offset(peer_agent.state.id, default=0)
        unread_messages = messages[read_offset:]
        self._set_read_offset(peer_agent.state.id, len(messages))
        return unread_messages

    def make_outgoing_start_metadata(
        self, chat: mdl.ChatDescriptor
    ) -> tuple[mdl.BasicStartMessageMetadata,
               type[mdl.BasicChatMessageMetadata]]:
        if chat.channel != "agent":
            raise ValueError(f"got descriptor for {chat.channel}")
        return (
            mdl.BasicStartMessageMetadata(chat=chat),
            mdl.BasicChatMessageMetadata)

    async def send(self, message: msg.AgentMessage) -> None:
        assert message.metadata.chat.channel == "agent"
        recipient = self._get_agent(message.metadata.chat.chat_id)
        try:
            recipient_channel = recipient.channels["agent"]
        except KeyError:
            raise base.ChannelError(
                "recipient doesn't have an agent channel to send to")
        assert isinstance(recipient_channel, AgentChannel)
        chat_message = await message.model
        messages = self._messages.setdefault(recipient.state.id, [])
        read_offset = self._get_read_offset(recipient.state.id, default=0)
        if len(messages) != read_offset:
            raise base.ChannelError("can't send if there are unread messages")
        await self._append_message(recipient.state.id, chat_message)
        self._set_read_offset(recipient.state.id, len(messages))
        await recipient_channel.add_incoming_agent_message(
            message, self._agent_id)

    async def add_incoming_agent_message(
            self, message: msg.AgentMessage, sender_id: uuid.UUID) -> None:
        """
        Add an incoming agent message.

        A user message will be constructed with the content of the given agent
        message. That message will appear has having arrived on the channel and
        will be delivered to the agent.
        """
        metadata = mdl.BasicChatMessageMetadata(
            time=await message.metadata.time.value,
            chat=mdl.BasicChatDescriptor(
                channel=self.type, chat_id=str(sender_id)))
        message = mdl.ChatMessage(
            role="user", metadata=metadata, content=await message.content)
        await self._append_message(sender_id, message)
        await self._publisher.append(message)
