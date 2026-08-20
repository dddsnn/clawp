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

import pathlib
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

    def __init__(
            self, messages_dir: pathlib.Path,
            state: mdl.WebUiChannelState) -> None:
        """
        :param state: A config object storing persistent state. This contains
            the read offset, which is updated inside that instance and must be
            persisted externally.
        """
        super().__init__("web_ui")
        self._state = state
        self._messages: list[mdl.UserMessage | mdl.AgentMessage] = []
        self._messages_io = store.JsonlIO(
            messages_dir / self._MESSAGES_FILE_NAME, mdl.MessageTypeAdapter)

    async def start(self, agent: agt.Agent) -> None:
        await super().start(agent)
        await self._load_messages_from_disk()
        if not 0 <= self._read_offset <= len(self._messages):
            self._logger.error(
                f"Read offset on disk is invalid (was {self._read_offset}, "
                f"with {len(self._messages)} messages. Resetting to end.")
            self._read_offset = len(self._messages)

    async def stop(self) -> None:
        await self._messages_io.close()
        await super().stop()

    @property
    def _read_offset(self) -> int:
        return self._state.read_offset

    @_read_offset.setter
    def _read_offset(self, value: int) -> None:
        self._state.read_offset = value

    async def _load_messages_from_disk(self) -> None:
        try:
            models = [model async for model in self._messages_io.read_all()]
        except FileNotFoundError:
            models = []
        messages: list[mdl.UserMessage | mdl.AgentMessage] = []
        for model in models:
            if not isinstance(model, (mdl.UserMessage, mdl.AgentMessage)):
                raise base.ChannelError(
                    f"web_ui channel file contains message type {type(model)} "
                    "(only user and agent messages allowed)")
            if (model.metadata.chat.channel != "web_ui"
                    or model.metadata.chat.chat_id != ""):
                raise base.ChannelError(
                    "web_ui channel file contains message for wrong chat")
            messages.append(model)
        self._messages = messages

    async def _append_message(
            self, message: mdl.UserMessage | mdl.AgentMessage) -> None:
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
        self._assert_valid_chat_id(chat_id)
        return mdl.BasicChatDescriptor(channel=self.type, chat_id=chat_id)

    def _assert_valid_chat_id(self, chat_id: str) -> None:
        if chat_id != "":
            raise base.ChatIdError("invalid chat_id (use empty string \"\")")

    async def num_unread_messages(self, chat_id: str) -> int:
        self._assert_valid_chat_id(chat_id)
        return len(self._messages) - self._read_offset

    async def get_unread_messages(self,
                                  chat_id: str) -> list[mdl.IncomingMessage]:
        self._assert_valid_chat_id(chat_id)
        unread_messages = self._messages[self._read_offset:]
        self._read_offset = len(self._messages)
        incoming_messages = []
        for m in unread_messages:
            if isinstance(m, mdl.AgentMessage):
                self._logger.warning(
                    "Skipping agent message that was part of unread messages, "
                    "probably due to unclean shutdown.")
                continue
            incoming_messages.append(self._make_incoming_message(m))
        return incoming_messages

    def _make_incoming_message(
            self, message: mdl.UserMessage | mdl.AgentMessage
    ) -> mdl.IncomingMessage:
        return mdl.IncomingMessage(chat=message.metadata.chat, message=message)

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
        chat = mdl.BasicChatDescriptor(channel=self.type, chat_id="")
        metadata = mdl.BasicChatMessageMetadata(time=time, chat=chat)
        message = mdl.UserMessage(metadata=metadata, content=content)
        await self._append_message(message)
        await self._publisher.append(self._make_incoming_message(message))


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
            messages_dir: pathlib.Path, state: mdl.AgentChannelState) -> None:
        """
        :param state: A config object storing persistent state. This contains
            the read offsets, which are updated inside that instance and must
            be persisted externally.
        """
        super().__init__("agent")
        self._agent_id = agent_id
        self._agent_repo = agent_repo
        self._messages_dir = messages_dir
        self._state = state
        self._messages: dict[uuid.UUID,
                             list[mdl.UserMessage | mdl.AgentMessage]] = {}
        self._chat_ios: dict[uuid.UUID, store.JsonlIO] = {}

    async def start(self, agent: agt.Agent) -> None:
        await super().start(agent)
        await self._load_messages_from_disk()

    async def stop(self) -> None:
        for io in self._chat_ios.values():
            await io.close()
        self._chat_ios.clear()
        await super().stop()

    def _messages_path(self, peer_agent_id: uuid.UUID):
        return self._messages_dir / f"{peer_agent_id}.jsonl"

    def _io_for_chat(self, peer_agent_id: uuid.UUID) -> store.JsonlIO:
        try:
            return self._chat_ios[peer_agent_id]
        except KeyError:
            io = store.JsonlIO(
                self._messages_path(peer_agent_id), mdl.MessageTypeAdapter)
            self._chat_ios[peer_agent_id] = io
            return io

    async def _load_messages_from_disk(self) -> None:
        if not self._messages_dir.exists():
            return
        for path in self._messages_dir.iterdir():
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
            chat_messages: list[mdl.UserMessage | mdl.AgentMessage] = []
            for model in models:
                if not isinstance(model, (mdl.UserMessage, mdl.AgentMessage)):
                    raise base.ChannelError(
                        "agent channel file contains message of type "
                        f"{type(model)} (only user and agent messages allowed)"
                    )
                if (model.metadata.chat.channel != "agent"
                        or model.metadata.chat.chat_id != str(peer_agent_id)):
                    raise base.ChannelError(
                        "agent channel file contains message for wrong chat")
                chat_messages.append(model)
            self._messages[peer_agent_id] = chat_messages

    def _get_read_offset(
            self, peer_agent_id: uuid.UUID, *, default: int) -> int:
        try:
            return self._state.read_offsets[peer_agent_id]
        except KeyError:
            return default

    def _set_read_offset(
            self, peer_agent_id: uuid.UUID, read_offset: int) -> None:
        messages = self._messages.setdefault(peer_agent_id, [])
        if not 0 <= read_offset <= len(messages):
            raise ValueError("new offset is not in valid range")
        self._state.read_offsets[peer_agent_id] = read_offset

    async def _append_message(
            self, peer_agent_id: uuid.UUID,
            message: mdl.UserMessage | mdl.AgentMessage) -> None:
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

    async def num_unread_messages(self, chat_id: str) -> int:
        peer_agent = self._get_agent(chat_id)
        messages = self._messages.setdefault(peer_agent.information.id, [])
        read_offset = self._get_read_offset(
            peer_agent.information.id, default=0)
        return len(messages) - read_offset

    async def get_unread_messages(self,
                                  chat_id: str) -> list[mdl.IncomingMessage]:
        peer_agent = self._get_agent(chat_id)
        messages = self._messages.setdefault(peer_agent.information.id, [])
        read_offset = self._get_read_offset(
            peer_agent.information.id, default=0)
        unread_messages = messages[read_offset:]
        self._set_read_offset(peer_agent.information.id, len(messages))
        incoming_messages = []
        for m in unread_messages:
            if isinstance(m, mdl.AgentMessage):
                self._logger.warning(
                    "Skipping agent message that was part of unread messages, "
                    "probably due to unclean shutdown.")
                continue
            incoming_messages.append(self._make_incoming_message(m))
        return incoming_messages

    def _make_incoming_message(
            self, message: mdl.UserMessage | mdl.AgentMessage
    ) -> mdl.IncomingMessage:
        return mdl.IncomingMessage(chat=message.metadata.chat, message=message)

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
        messages = self._messages.setdefault(recipient.information.id, [])
        read_offset = self._get_read_offset(
            recipient.information.id, default=0)
        if len(messages) != read_offset:
            raise base.ChannelError("can't send if there are unread messages")
        await self._append_message(recipient.information.id, chat_message)
        self._set_read_offset(recipient.information.id, len(messages))
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
        chat = mdl.BasicChatDescriptor(
            channel=self.type, chat_id=str(sender_id))
        metadata = mdl.BasicChatMessageMetadata(
            time=await message.metadata.time.value, chat=chat)
        message = mdl.UserMessage(
            metadata=metadata, content=await message.content)
        await self._append_message(sender_id, message)
        await self._publisher.append(self._make_incoming_message(message))
