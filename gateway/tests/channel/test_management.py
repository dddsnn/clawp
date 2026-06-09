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

import dataclasses as dc
import pathlib

import pytest
from hamcrest import (
    all_of,
    assert_that,
    contains_inanyorder,
    has_properties,
    instance_of,
)

from clawp import channel as chan
from clawp import model as mdl


def matrix_channel_matching_config(config: mdl.MatrixConfig, username: str):
    account = next(a for a in config.accounts if a.username == username)
    expected_channel = all_of(
        instance_of(chan.MatrixChannel),
        has_properties(
            id=username, _config=account, _client=has_properties(
                store_path=str(config.store_dir.resolve()),
                homeserver=account.homeserver, user=username,
                device_id=account.device_id)))
    return all_of(
        instance_of(chan.PoolChannelStatus),
        has_properties(channel=expected_channel, config=account))


@dc.dataclass
class MockChannel:
    type: str
    id: str


class TestChannelPool:
    def make_channels_config(
            self, matrix_usernames: list[str]) -> mdl.ChannelsConfig:
        matrix_accounts = [
            mdl.MatrixAccountConfig(
                homeserver=f"homeserver_{username}", username=username,
                password=f"password_{username}",
                device_id=f"device_id_{username}")
            for username in matrix_usernames]
        return mdl.ChannelsConfig(
            matrix=mdl.MatrixConfig(
                store_dir=pathlib.Path("store/dir"), accounts=matrix_accounts))

    async def test_acquire_raises_with_no_channels(self):
        pool = chan.ChannelPool(self.make_channels_config([]))
        with pytest.raises(chan.NoSuchChannelError):
            pool.acquire(mdl.ClaimedChannel(type="matrix", id="id1"))

    async def test_acquire_raises_with_no_matching_channel_id(self):
        pool = chan.ChannelPool(self.make_channels_config(["id1", "id3"]))
        with pytest.raises(chan.NoSuchChannelError):
            pool.acquire(mdl.ClaimedChannel(type="matrix", id="id2"))

    async def test_acquire_raises_with_no_matching_channel_type(self):
        pool = chan.ChannelPool(self.make_channels_config(["id1", "id3"]))
        with pytest.raises(chan.NoSuchChannelError):
            pool.acquire(
                mdl.ClaimedChannel.model_construct(
                    type="not_matrix", id="id1"))

    async def test_acquire_returns_available_channel(self):
        config = self.make_channels_config(["id1"])
        pool = chan.ChannelPool(config)
        assert_that(
            pool.acquire(mdl.ClaimedChannel(type="matrix", id="id1")),
            matrix_channel_matching_config(config.matrix, "id1"))

    async def test_acquire_raises_if_channel_has_been_acquired(self):
        config = self.make_channels_config(["id1"])
        pool = chan.ChannelPool(config)
        pool.acquire(mdl.ClaimedChannel(type="matrix", id="id1"))
        with pytest.raises(chan.ChannelStateError):
            pool.acquire(mdl.ClaimedChannel(type="matrix", id="id1"))

    async def test_acquire_release_acquire(self):
        config = self.make_channels_config(["id1"])
        pool = chan.ChannelPool(config)
        channel_status = pool.acquire(
            mdl.ClaimedChannel(type="matrix", id="id1"))
        pool.release(channel_status.channel)
        assert pool.acquire(mdl.ClaimedChannel(type="matrix", id="id1"))

    async def test_release_raises_with_no_matching_channel_id(self):
        config = self.make_channels_config(["id1"])
        pool = chan.ChannelPool(config)
        with pytest.raises(chan.NoSuchChannelError):
            pool.release(MockChannel("matrix", "id2"))

    async def test_release_raises_with_no_matching_channel_type(self):
        config = self.make_channels_config(["id1"])
        pool = chan.ChannelPool(config)
        with pytest.raises(chan.NoSuchChannelError):
            pool.release(MockChannel("not_matrix", "id2"))

    async def test_release_raises_if_channel_had_not_been_acquired(self):
        config = self.make_channels_config(["id1"])
        pool = chan.ChannelPool(config)
        channel_status = pool.acquire(
            mdl.ClaimedChannel(type="matrix", id="id1"))
        pool.release(channel_status.channel)
        with pytest.raises(chan.ChannelStateError):
            pool.release(channel_status.channel)

    async def test_iter(self):
        channels_config = self.make_channels_config(["id1", "id2"])
        pool = chan.ChannelPool(channels_config)
        matrix_accounts = channels_config.matrix.accounts
        expected_stati = [
            has_properties(
                channel=has_properties(id=a.id), config=a, status="available")
            for a in matrix_accounts]
        assert_that(list(pool), contains_inanyorder(*expected_stati))

    async def test_iter_shows_channels_acquired(self):
        channels_config = self.make_channels_config(["id1", "id2"])
        pool = chan.ChannelPool(channels_config)
        matrix_accounts = channels_config.matrix.accounts
        pool.acquire(mdl.ClaimedChannel(type="matrix", id="id1"))
        expected_stati = [
            has_properties(
                channel=has_properties(id=a.id), config=a,
                status="acquired" if a.id == "id1" else "available")
            for a in matrix_accounts]
        assert_that(list(pool), contains_inanyorder(*expected_stati))
