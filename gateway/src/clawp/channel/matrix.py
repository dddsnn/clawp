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
import typing as t

import nio
import whenever as we

from .. import message as msg
from .. import model as mdl
from .. import util
from . import base


class MatrixChannel(base.Channel):
    """
    Matrix channel.

    This channel is used to communicate via Matrix. Credentials are
    configured on construction.
    """
    def __init__(self, config: mdl.MatrixConfig) -> None:
        super().__init__("matrix")
        self._config = config
        client_config = nio.AsyncClientConfig(
            encryption_enabled=True, store_sync_tokens=True)
        self._client = nio.AsyncClient(
            self._config.homeserver, self._config.username,
            device_id=self._config.device_id,
            store_path=str(self._config.store_dir.resolve()),
            config=client_config)
        self._client.add_event_callback(
            self._on_room_message_text, nio.RoomMessageText)
        self._sync_forever_task = None

    async def __aenter__(self) -> t.Self:
        await super().__aenter__()
        await self._client.login(self._config.password)
        self._client.load_store()
        self._sync_forever_task = asyncio.create_task(
            self._client.sync_forever())
        return self

    async def __aexit__(self, *args) -> bool:
        await super().__aexit__(*args)
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
                "Error closing underlying network connection.")
        return False

    @property
    async def status(self) -> str:
        return mdl.MatrixChannelStatus(
            type=self.type, available=True, username=self._config.username)

    async def send(self, message: msg.AgentMessage) -> None:
        channel = message.metadata.channel.value
        if not isinstance(channel, mdl.MatrixOutgoingChannelDescriptor):
            raise ValueError(
                "cannot send to Matrix without Matrix channel descriptor (got "
                f"{channel})")
        await self._client.room_send(
            channel.room_id, message_type="m.room.message",
            content={"msgtype": "m.text", "body": await message.content})

    def response_channel(
        self, incoming_descriptor: mdl.MatrixIncomingChannelDescriptor
    ) -> mdl.MatrixOutgoingChannelDescriptor:
        return mdl.MatrixOutgoingChannelDescriptor(
            room_id=incoming_descriptor.room_id)

    async def _on_room_message_text(
            self, room: nio.MatrixRoom, event: nio.RoomMessageText) -> None:
        if event.sender == self._config.username:
            # We will get events for messages we sent. Avoid feedback loops.
            return
        metadata = msg.IncomingMessageMetadata(
            time=util.ImmediateValue(
                we.Instant.from_timestamp_millis(event.server_timestamp)),
            channel=mdl.MatrixIncomingChannelDescriptor(
                room_id=room.room_id,
                room_name=room.named_room_name(),
                sender_id=event.sender,
                sender_name=room.user_name(event.sender),
            ))
        message = base.IncomingMessage(
            role="user", metadata=metadata, content=event.body,
            request_response=True)
        await self._publisher.append(message)
