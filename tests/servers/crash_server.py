"""A test MCP server whose crash tool kills the process mid-call."""

import os
import warnings

from fastmcp import FastMCP

warnings.filterwarnings("ignore")
mcp = FastMCP("crash")


@mcp.tool()
def crash() -> str:
    """Exit the server process."""
    os._exit(1)  # pylint: disable=protected-access


@mcp.tool()
def ping() -> str:
    """Reply pong."""
    return "pong"


if __name__ == "__main__":
    mcp.run(transport="stdio")
