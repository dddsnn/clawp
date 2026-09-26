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

import collections.abc as cl_abc
import dataclasses as dc
import logging
import pathlib
import typing as t
import uuid

import fastmcp
import fastmcp.client.client
import fastmcp.tools
import mcp.types
import pydantic as pyd
import whenever as we

from .. import file
from .. import model as mdl

if t.TYPE_CHECKING:
    from .. import agent as agt
    from . import client as clt

Iso8601Instant = t.Annotated[
    we.Instant,
    pyd.Field(
        description="An ISO 8601 timestamp", examples=["2026-06-14T17:53:00Z"]
    ),
]


class McpServer(fastmcp.FastMCP, file.InfoProvider):
    """
    Base MCP server that is used by an agent.

    This is a thin subclass around FastMCP. It can declare config file paths
    relative to the agent's workspace that are necessary for certain tools. By
    declaring them it signals that they need to be created if they don't exist,
    and that their content should be provided to the agent in an info message.

    Additionally, it can declare other info messages, e.g. tutorials for
    specific tools.
    """

    def __init__(self, name: str, agent: agt.Agent):
        super().__init__(name)
        self._agent = agent

    @property
    def config_file_paths(self) -> frozenset[pathlib.Path]:
        """
        Config files that are required for this server.

        Given a collection of tools that are enabled, returns a set of file
        paths relative to the agent's workspace that need to exist for the
        server to work correctly. These files must exist in the config_files
        resource of the file module.
        """
        return frozenset()

    @property
    def info_message_specs(self) -> frozenset[mdl.InfoMessageSpec[t.Any]]:
        return frozenset()


class ComplexToolResultMetadataRegistry:
    """
    Registry for complex, non-serializable MCP tool result metadata.

    FastMCP tool results can carry normal metadata, but that metadata must be
    serializable. We sometimes need to return process-local objects (e.g.
    closures that perform session operations) together with the tool result.
    This registry stores such objects out-of-band and links them to the FastMCP
    result via an opaque ID in the result's regular metadata.

    The registry is in-memory and process-local by design.
    """

    def __init__(self) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._registry: dict[str, dict[str, t.Any]] = {}

    def make_result(
        self,
        content: list[mcp.types.ContentBlock] | str | t.Any,
        structured_content: dict[str, t.Any] | t.Any | None = None,
        **complex_metadata: t.Any,
    ) -> fastmcp.tools.ToolResult:
        """
        Create a FastMCP ToolResult and attach complex metadata.

        The provided complex_metadata entries are stored in this registry under
        a generated ID. That ID is embedded in the result's normal metadata,
        allowing a process-local client to fetch the complex metadata later.

        :param content: ToolResult content passed through to FastMCP.
        :param structured_content: Structured content passed through to FastMCP.
        :param complex_metadata: Arbitrary non-serializable values to associate
            with this result.
        :returns: A ToolResult containing the generated metadata reference ID.
        """
        complex_metadata_id = str(uuid.uuid4())
        self._registry[complex_metadata_id] = complex_metadata
        return fastmcp.tools.ToolResult(
            content=content,
            structured_content=structured_content,
            meta={"complex_metadata_id": complex_metadata_id},
        )

    def pop_for_result(
        self, result: fastmcp.client.client.CallToolResult
    ) -> dict[str, t.Any]:
        """
        Get and remove complex metadata referenced by a ToolResult.

        If the result does not reference complex metadata, or if the reference
        is stale/missing, an empty dict is returned.

        :param result: The ToolResult carrying the metadata reference.
        :returns: The associated complex metadata dictionary.
        """
        try:
            assert result.meta is not None
            complex_metadata_id = result.meta["complex_metadata_id"]
        except AssertionError, KeyError:
            return {}
        try:
            return self._registry.pop(complex_metadata_id)
        except KeyError:
            self._logger.exception(
                f"Result {result} specified a complex metadata ID, but it "
                f"wasn't present in the registry."
            )
            return {}


@dc.dataclass
class ToolResult:
    """The result of a tool call."""

    raw_result: fastmcp.client.client.CallToolResult
    """The result as returned by fastmcp."""
    content_string: str
    """The result content that can be presented to the agent."""


@dc.dataclass
class SessionOperationToolResult(ToolResult):
    """A tool result instructing an operation on the session."""

    operation: cl_abc.Callable[
        [agt.SessionTransaction], cl_abc.Awaitable[None]
    ]
    """An operation that should be performed on the session transaction."""


class ClientSessionTransactionContext:
    """
    Client proxy setting the session transaction.

    This is just a thin wrapper around a Client that acts as a context manager
    which sets and unsets a session transaction on the client.
    """

    def __init__(self, client: clt.Client, tx: agt.SessionTransaction) -> None:
        self._client = client
        self._tx = tx

    def __enter__(self) -> t.Self:
        self._client.set_session_transaction(self._tx)
        return self

    def __exit__(self, *args) -> bool:
        self._client.set_session_transaction(None)
        return False

    async def call_tool(self, name: str, *args, **kwargs) -> ToolResult:
        return await self._client.call_tool(name, *args, **kwargs)
