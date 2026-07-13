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

import pydantic as pyd

from . import base
from . import config as cfg

ChannelType = t.Literal["agent", "matrix", "web_ui"]


class BasicChatDescriptor(base.BaseModel):
    channel: t.Literal["agent", "web_ui"]
    chat_id: str


class MatrixChatDescriptor(BasicChatDescriptor):
    channel: t.Literal["matrix"] = "matrix"
    room_name: t.Optional[str]

    def __eq__(self, other: any) -> bool:
        # channel/chat_id are enough for equality, room_name is optional.
        if not isinstance(other, ChatDescriptor):
            return NotImplemented
        self_dict = self.model_dump(exclude={"room_name"})
        other_dict = other.model_dump(exclude={"room_name"})
        return self_dict == other_dict

    def __hash__(self) -> int:
        return hash((self.channel, self.room_name))


ChatDescriptor = BasicChatDescriptor | MatrixChatDescriptor

ChannelConfig = t.Annotated[cfg.MatrixAccountConfig,
                            pyd.Field(discriminator="type")]


class BaseChannelStatus(base.BaseModel):
    type: ChannelType
    available: bool


class WebUiChannelStatus(BaseChannelStatus):
    type: t.Literal["web_ui"] = "web_ui"


class AgentChannelStatus(BaseChannelStatus):
    type: t.Literal["agent"] = "agent"


class MatrixChannelStatus(BaseChannelStatus):
    type: t.Literal["matrix"] = "matrix"
    username: str


ChannelStatus = t.Annotated[WebUiChannelStatus
                            | AgentChannelStatus | MatrixChannelStatus,
                            pyd.Field(discriminator="type")]


class ChannelInformation(base.BaseModel):
    type: ChannelType
    id: t.Optional[str]
    config: ChannelConfig
    status: ChannelStatus
    assigned_to_agent: t.Optional[uuid.UUID]

    @pyd.model_validator(mode="after")
    def check_type_is_consistent(self) -> t.Self:
        if self.type != self.status.type:
            raise ValueError("type and status.type differ")
        return self
