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


class ChatSwitchError(RuntimeError):
    """Raised when there is an error switching chat."""


class CompactionError(RuntimeError):
    """Raised when there is an error compacting an agent's session."""


class MissingToolError(Exception):
    """Raised when an agent is missing required tools."""

    def __init__(self, message: str, missing_tools: frozenset[str]) -> None:
        super().__init__(message)
        self.missing_tools = missing_tools
