"""MCP server: publishes the tools of tools.py with the Model Context Protocol.

It is mounted inside the FastAPI app (see main.py) at  http://<host>:8000/mcp/
so `uvicorn app.main:app` starts both. Any MCP client (our LangGraph agent,
Claude Desktop, MCP Inspector...) can list and call these tools.
"""

import asyncio
import functools

from mcp.server.fastmcp import FastMCP

from . import tools



def _as_async(fn):
    """The tools are normal (blocking) functions: run them in a thread so the
    server keeps answering other requests while a model is working.
    functools.wraps keeps the name, docstring and parameters, which MCP uses
    to describe the tool to the model."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
    return wrapper


def build_mcp() -> FastMCP:
    """Create the MCP server with all our tools.
    (A function, because the HTTP session manager can only be started once
    per server: each start of the API builds a fresh one.)"""
    server = FastMCP(
        "emsi-document",
        stateless_http=True,        # every request stands alone: simple, robust
        json_response=True,         # plain JSON answers instead of a stream
        streamable_http_path="/",   # mounted at /mcp in main.py → /mcp/
    )
    for tool in tools.ALL_TOOLS:
        server.add_tool(_as_async(tool), name=tool.__name__,
                        description=tool.__doc__)
    return server


if __name__ == "__main__":
    # Optional: run the MCP server alone (stdio), e.g. for MCP Inspector.
    build_mcp().run()
