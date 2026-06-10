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

ChannelType = t.Literal["matrix", "system", "web_ui"]


class BaseChannelDescriptor(base.BaseModel):
    type: ChannelType


class SystemChannelDescriptor(BaseChannelDescriptor):
    type: t.Literal["system"] = "system"


class WebUiChannelDescriptor(BaseChannelDescriptor):
    type: t.Literal["web_ui"] = "web_ui"


class MatrixOutgoingChannelDescriptor(BaseChannelDescriptor):
    type: t.Literal["matrix"] = "matrix"
    room_id: str


class MatrixIncomingChannelDescriptor(MatrixOutgoingChannelDescriptor):
    room_name: t.Optional[str]
    sender_id: str
    sender_name: t.Optional[str]


IncomingChannelDescriptor = t.Annotated[SystemChannelDescriptor
                                        | WebUiChannelDescriptor
                                        | MatrixIncomingChannelDescriptor,
                                        pyd.Field(discriminator="type")]
"""
Channel descriptor for incoming messages.

Incoming messages are ones sent to the agent.
"""
IncomingChannelDescriptorTypeAdapter = pyd.TypeAdapter(
    IncomingChannelDescriptor)
OutgoingChannelDescriptor = t.Annotated[SystemChannelDescriptor
                                        | WebUiChannelDescriptor
                                        | MatrixOutgoingChannelDescriptor,
                                        pyd.Field(discriminator="type")]
"""
Channel descriptor for outgoing messages.

Outgoing messages are ones sent to by agent to the outside.
"""
OutgoingChannelDescriptorTypeAdapter = pyd.TypeAdapter(
    OutgoingChannelDescriptor)

ChannelDescriptor = IncomingChannelDescriptor | OutgoingChannelDescriptor

ChannelConfig = t.Annotated[cfg.MatrixAccountConfig,
                            pyd.Field(discriminator="type")]


class BaseChannelStatus(base.BaseModel):
    type: ChannelType
    available: bool


class MatrixChannelStatus(BaseChannelStatus):
    type: t.Literal["matrix"] = "matrix"
    username: str


ChannelStatus = t.Annotated[MatrixChannelStatus,
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
