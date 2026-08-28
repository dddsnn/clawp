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


class ShellResult(base.BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    shell: str


class SaveActionConfig(base.BaseModel):
    """
    Config for file system save actions.

    The save_actions dict maps each file extension (starting with ".", e.g.
    ".py" to a list of save action commands that should be executed in the
    shell.
    """

    save_actions: dict[
        t.Annotated[str, pyd.StringConstraints(pattern=r"^\.[^.\s]+$")],
        list[str],
    ]
