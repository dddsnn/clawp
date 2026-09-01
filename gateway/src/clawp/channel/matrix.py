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
import dataclasses as dc
import pathlib
import typing as t

import nio
import whenever as we

from .. import agent as agt
from .. import message as msg
from .. import model as mdl
from . import base


class MatrixChannel(base.Channel):
    """
    Matrix channel.

    This channel is used to communicate via Matrix. Credentials are
    configured on construction.
    """

    @dc.dataclass
    class RoomMessageEvent:
        room: nio.MatrixRoom
        event: nio.RoomMessageText
        incoming_message: mdl.IncomingMessage

    def __init__(
        self, store_dir: pathlib.Path, config: mdl.MatrixAccountConfig
    ) -> None:
        super().__init__("matrix")
        self._config = config
        client_config = nio.AsyncClientConfig(
            encryption_enabled=True, store_sync_tokens=True
        )
        self._client = nio.AsyncClient(
            self._config.homeserver,
            self._config.username,
            device_id=self._config.device_id,
            store_path=str(store_dir.resolve()),
            config=client_config,
        )
        self._client.add_event_callback(
            self._on_room_message_text,  # pyright: ignore[reportArgumentType]
            nio.RoomMessageText,
        )
        self._unread_message_events: dict[
            str, list[MatrixChannel.RoomMessageEvent]
        ] = {}

    async def start(self, agent: agt.Agent) -> None:
        await super().start(agent)
        await self._client.login(self._config.password.value)
        self._client.load_store()
        self._sync_forever_task = asyncio.create_task(
            self._client.sync_forever()
        )
        await self._client.set_displayname(
            self._agent.information.name_with_agent_tag
        )

    async def stop(self) -> None:
        await super().stop()
        try:
            self._client.stop_sync_forever()
            self._sync_forever_task.cancel()
            async with asyncio.timeout(20):
                await self._sync_forever_task
        except asyncio.CancelledError:
            pass
        except Exception:
            self._logger.exception("Error stopping client sync.")
        try:
            async with asyncio.timeout(5):
                await self._client.close()
        except Exception:
            self._logger.exception(
                "Error closing underlying network connection."
            )

    @property
    def type(self) -> t.Literal["matrix"]:
        return "matrix"

    @property
    def id(self) -> str:
        return self._config.username

    @property
    async def status(self) -> mdl.MatrixChannelStatus:
        return mdl.MatrixChannelStatus(
            available=True, username=self._config.username
        )

    async def get_chat_descriptor(
        self, chat_id: str
    ) -> mdl.MatrixChatDescriptor:
        return mdl.MatrixChatDescriptor(chat_id=chat_id, room_name=None)

    async def num_unread_messages(self, chat_id: str) -> int:
        try:
            return len(self._unread_message_events[chat_id])
        except KeyError:
            raise base.ChatIdError(f"no room {chat_id}")

    async def get_unread_messages(
        self, chat_id: str
    ) -> list[mdl.IncomingMessage]:
        try:
            room_message_events = self._unread_message_events[chat_id]
        except KeyError:
            raise base.ChatIdError(f"no room {chat_id}")
        if not room_message_events:
            return []
        last_event_id = room_message_events[-1].event.event_id
        resp = await self._client.room_read_markers(
            room_id=chat_id,
            fully_read_event=last_event_id,
            read_event=last_event_id,
        )
        if isinstance(resp, nio.RoomReadMarkersError):
            raise base.ChannelError(f"error in updating read marker: {resp}")
        self._unread_message_events[chat_id] = []
        return [rme.incoming_message for rme in room_message_events]

    def make_outgoing_start_metadata(
        self, chat: mdl.MatrixChatDescriptor
    ) -> tuple[
        mdl.MatrixStartMessageMetadata,
        type[mdl.MatrixChatMessageMetadata],
    ]:
        start_metadata = mdl.MatrixStartMessageMetadata(
            chat=chat,
            sender_id=self.id,
            sender_name=self._agent.information.name_with_agent_tag,
        )
        return start_metadata, mdl.MatrixChatMessageMetadata

    async def send(self, message: msg.AgentMessage) -> None:
        assert message.metadata.chat.channel == "matrix"
        if self._unread_message_events.get(message.metadata.chat.chat_id, []):
            raise base.ChannelError("can't send if there are unread messages")
        await self._client.room_send(
            message.metadata.chat.chat_id,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": await message.content},
        )

    async def _on_room_message_text(
        self, room: nio.MatrixRoom, event: nio.RoomMessageText
    ) -> None:
        if event.sender == self._config.username:
            # We will get events for messages we sent. Avoid feedback loops.
            return
        room_message_event = self._make_room_message_event(room, event)
        self._unread_message_events.setdefault(room.room_id, []).append(
            room_message_event
        )
        await self._publisher.append(room_message_event.incoming_message)

    def _make_room_message_event(
        self, room: nio.MatrixRoom, event: nio.RoomMessageText
    ) -> RoomMessageEvent:
        sender_name = room.user_name(event.sender)
        if sender_name is None:
            sender_name = "<unknown>"
        metadata = mdl.MatrixChatMessageMetadata(
            time=we.Instant.from_timestamp_millis(event.server_timestamp),
            chat=mdl.MatrixChatDescriptor(
                chat_id=room.room_id, room_name=room.named_room_name()
            ),
            sender_id=event.sender,
            sender_name=sender_name,
        )
        message = mdl.MatrixChatMessage(
            role="user", metadata=metadata, content=event.body
        )
        incoming_message = mdl.IncomingMessage(
            chat=message.metadata.chat, message=message
        )
        return self.RoomMessageEvent(
            room=room, event=event, incoming_message=incoming_message
        )

    @property
    def info_message_specs(self) -> frozenset[mdl.InfoMessageSpec[t.Any]]:
        return frozenset([mdl.InfoMessageSpecTutorial(topic="channel_matrix")])
