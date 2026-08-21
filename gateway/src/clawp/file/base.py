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
import importlib.resources
import pathlib


def do_with_resource_dir(resource_name: str, function):
    resource = importlib.resources.files(f"clawp.file.{resource_name}")
    with importlib.resources.as_file(resource) as resource_dir:
        return function(resource_dir)


async def read_file(resource_name: str, file_path: pathlib.Path) -> str:
    """Read a file in the given resource."""

    def read(resource_dir: pathlib.Path):
        if file_path.is_absolute():
            raise ValueError(
                "file_path must be relative to the resource directory"
            )
        absolute_path = resource_dir / file_path
        with absolute_path.open() as f:
            return f.read()

    return await asyncio.to_thread(do_with_resource_dir, resource_name, read)
