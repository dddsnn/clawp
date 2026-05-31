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

import ruamel.yaml

from .. import model as mdl
from . import base


async def list_personalities() -> list[str]:
    """List the names of all available agent personalities."""
    def list_personalities(personalities_dir: pathlib.Path):
        return sorted(
            file.name.removesuffix(".yaml")
            for file in personalities_dir.glob("*.yaml"))

    return await asyncio.to_thread(
        base.do_with_resource_dir, "personalities", list_personalities)


async def read_personality(name: str) -> mdl.AgentPersonality:
    """Read an agent personality by name."""
    def read_yaml(personalities_dir: pathlib.Path):
        yaml = ruamel.yaml.YAML()
        file_path = personalities_dir / f"{name}.yaml"
        return yaml.load(file_path)

    personality_dict = await asyncio.to_thread(
        base.do_with_resource_dir, "personalities", read_yaml)
    return mdl.AgentPersonality.model_validate(personality_dict)


async def read_personality_with_file_contents(
        name: str) -> mdl.AgentPersonalityWithFileContents:
    """Read an agent personality by name."""
    personality = await read_personality(name)
    file_contents = {}
    for pf in personality.personality_files:
        try:
            content = await base.read_file(
                "personalities",
                pathlib.Path(name) / pf.path)
        except FileNotFoundError:
            content = None
        file_contents[pf.path] = content
    return mdl.AgentPersonalityWithFileContents.model_validate(
        personality.model_dump()
        | {"personality_file_contents": file_contents})
