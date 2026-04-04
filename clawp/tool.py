import collections.abc as cl_abc

import fastmcp
import fastmcp.tools

mcp_server = fastmcp.FastMCP(name="Clawp MCP server")


@mcp_server.tool
def add(a: int, b: int) -> int:
    """Adds two integer numbers together."""
    return a + b


class Client:
    def __init__(self):
        self._client = fastmcp.Client(mcp_server)
        self._tools = None

    async def __aenter__(self):
        await self._client.__aenter__()
        self._tools = {t.name: t for t in await self._client.list_tools()}
        return self

    async def __aexit__(self, *args):
        await self._client.__aexit__(*args)
        self._tools = None
        return False

    @property
    def tools(self) -> dict[str, fastmcp.tools.Tool]:
        if self._tools is None:
            raise ValueError("client not initialized")
        return self._tools

    async def call_tool(
            self, name: str, *args,
            **kwargs) -> cl_abc.Awaitable[fastmcp.tools.CallToolResult]:
        if name not in self._tools:
            raise ValueError(f"unknown tool {name}")
        return await self._client.call_tool(name, *args, **kwargs)
