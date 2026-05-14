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
import pathlib
import typing as t

import nio
import whenever as we

import message as msg
import model as mdl
import util

from . import base


class MatrixChannel(base.Channel):
    """
    Matrix channel.

    This channel is used to communicate via Matrix. Credentials are
    configured on construction.
    """
    def __init__(
            self, homeserver: str, username: str, password: str,
            device_id: str, store_path: pathlib.Path) -> None:
        super().__init__("matrix")
        self._homeserver = homeserver
        self._username = username
        self._password = password
        client_config = nio.AsyncClientConfig(
            encryption_enabled=True, store_sync_tokens=True)
        self._client = nio.AsyncClient(
            homeserver, username, device_id=device_id,
            store_path=str(store_path.resolve()), config=client_config)
        self._client.add_event_callback(
            self._on_room_message_text, nio.RoomMessageText)
        self._sync_forever_task = None

    async def __aenter__(self) -> t.Self:
        await super().__aenter__()
        await self._client.login(self._password)
        self._client.load_store()
        self._sync_forever_task = asyncio.create_task(
            self._client.sync_forever())
        return self

    async def __aexit__(self, *args) -> bool:
        await super().__aexit__(*args)
        try:
            async with asyncio.timeout(20):
                self._client.stop_sync_forever()
                await self._client.close()
                await self._sync_forever_task
        except Exception:
            self._logger.exception("Error closing client.")
        return False

    @property
    async def channel_available_message(self) -> str:
        return await util.render_message_template(
            "channel_status", "matrix_available.md", username=self._username)

    async def send(self, message: msg.AgentMessage) -> None:
        channel = await message.metadata.channel.value
        if not isinstance(channel, mdl.MatrixOutgoingChannelDescriptor):
            raise ValueError(
                "cannot send to Matrix without Matrix channel descriptor (got "
                f"{channel})")
        await self._client.room_send(
            channel.room_id, message_type="m.room.message",
            content={"msgtype": "m.text", "body": await message.content})

    async def _on_room_message_text(
            self, room: nio.MatrixRoom, event: nio.RoomMessageText) -> None:
        if event.sender == self._username:
            # We will get events for messages we sent. Avoid feedback loops.
            return
        metadata = msg.IncomingMessageMetadata(
            time=util.ImmediateValue(
                we.Instant.from_timestamp_millis(event.server_timestamp)),
            channel=util.ImmediateValue(
                mdl.MatrixIncomingChannelDescriptor(
                    room_id=room.room_id,
                    room_name=room.named_room_name(),
                    sender_id=event.sender,
                    sender_name=room.user_name(event.sender),
                )))
        message = base.IncomingMessage(
            role="user", metadata=metadata, content=event.body,
            request_response=True)
        await self._publisher.append(message)
