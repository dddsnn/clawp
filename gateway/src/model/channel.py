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

import pydantic as pyd

from . import base

ChannelType = t.Literal["malformed", "missing", "system", "unknown", "web_ui"]


class BaseChannelDescriptor(base.BaseModel):
    type: ChannelType


class MalformedChannelDescriptor(BaseChannelDescriptor):
    type: t.Literal["malformed"] = "malformed"
    error_message: str


class SystemChannelDescriptor(BaseChannelDescriptor):
    type: t.Literal["system"] = "system"


class UnknownChannelDescriptor(BaseChannelDescriptor):
    type: t.Literal["unknown"] = "unknown"


class WebUiChannelDescriptor(BaseChannelDescriptor):
    type: t.Literal["web_ui"] = "web_ui"


class MissingChannelDescriptor(BaseChannelDescriptor):
    type: t.Literal["missing"] = "missing"
    fallback_channel: "ChannelDescriptor" = UnknownChannelDescriptor()


ChannelDescriptor = t.Annotated[MalformedChannelDescriptor
                                | MissingChannelDescriptor
                                | SystemChannelDescriptor
                                | UnknownChannelDescriptor
                                | WebUiChannelDescriptor,
                                pyd.Field(discriminator="type")]
ChannelDescriptorTypeAdapter = pyd.TypeAdapter(ChannelDescriptor)
