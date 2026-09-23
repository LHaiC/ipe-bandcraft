"""Minimal MCP v2 server used by the M0 smoke test.

Exposes one structured tool and one image-content tool over stdio.
"""

import base64
import sys

# ensure local src/ is importable when run as a script
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import anyio  # noqa: E402
from mcp.server import MCPServer  # noqa: E402
from mcp.types import ImageContent, TextContent  # noqa: E402
from pydantic import BaseModel  # noqa: E402

# 1x1 red PNG
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

server = MCPServer(name="ibc-m0-smoke", version="0.0.1")


class EchoResult(BaseModel):
    echo: str
    ok: bool


@server.tool(name="echo", description="Echo a value back in structured content", structured_output=True)
def echo(value: str) -> EchoResult:
    return EchoResult(echo=value, ok=True)


@server.tool(name="pixel", description="Return a 1x1 PNG image")
def pixel() -> list:
    return [
        TextContent(type="text", text="1x1 png"),
        ImageContent(type="image", data=base64.b64encode(PNG_1PX).decode(), mimeType="image/png"),
    ]


if __name__ == "__main__":
    # logging must go to stderr only — stdout is the protocol channel
    print("m0 smoke server starting", file=sys.stderr)
    anyio.run(server.run_stdio_async)
