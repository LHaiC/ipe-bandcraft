"""M0 smoke-test client: drives the minimal server over real stdio."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import anyio
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters


async def main() -> int:
    server_py = Path(__file__).with_name("m0_smoke_server.py")
    params = StdioServerParameters(command=sys.executable, args=[str(server_py)])
    async with Client(params, raise_exceptions=True) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools.tools]
        r1 = await client.call_tool("echo", {"value": "hello"})
        r2 = await client.call_tool("pixel", {})
        structured = getattr(r1, "structuredContent", None) or getattr(r1, "structured_content", None)
        out = {
            "tools": names,
            "echo_structured": structured,
            "echo_isError": r1.is_error,
            "pixel_blocks": [b.type for b in r2.content],
            "pixel_isError": r2.is_error,
        }
        print(json.dumps(out))
        ok = (
            "echo" in names
            and "pixel" in names
            and not r1.is_error
            and not r2.is_error
            and (structured or {}).get("echo") == "hello"
            and "image" in out["pixel_blocks"]
        )
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(anyio.run(main))
