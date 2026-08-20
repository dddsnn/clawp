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

import pydantic as pyd

from . import base


class AgentPersonalityFile(base.BaseModel):
    path: pathlib.Path
    description: str


class AgentPersonality(base.BaseModel):
    name: str
    personality_files: list[AgentPersonalityFile]


class AgentPersonalityWithFileContents(AgentPersonality):
    personality_file_contents: dict[pathlib.Path, t.Optional[str]]
    """
    File content for each of the personality files.

    A content of None indicates that the file is missing.
    """
    @pyd.model_validator(mode="after")
    def check_all_file_contents_present(self) -> t.Self:
        for pf in self.personality_files:
            if pf.path not in self.personality_file_contents:
                raise ValueError(
                    f"no content for personality file at {pf.path}")
        return self

    def get_personality(self) -> AgentPersonality:
        return AgentPersonality(
            name=self.name, personality_files=self.personality_files)


class AgentInformation(base.BaseModel):
    """Immutable agent information."""
    id: uuid.UUID
    name: t.Annotated[
        str, pyd.StringConstraints(strip_whitespace=True, min_length=1)]
    personality: AgentPersonality
