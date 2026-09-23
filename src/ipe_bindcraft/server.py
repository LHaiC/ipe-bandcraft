"""MCP stdio server — thin adapter over Service.

Conventions:
- stdout is reserved for the protocol; all logging goes to stderr.
- Business failures raise inside handlers; the SDK maps them to
  ``isError=true`` results. We convert IbcError to McpError carrying the
  stable code + details in the message payload.
- render_preview returns ImageContent; everything else returns a dict
  (serialized as JSON text + structured content when supported).
"""

from __future__ import annotations

import base64
import sys
from typing import Any

import anyio
from mcp.server import MCPServer
from mcp.types import ImageContent, TextContent
from pydantic import TypeAdapter, ValidationError

from .errors import IbcError
from .schemas import (
    ApplyOperationsInput,
    Operation,
    check_finite_recursive,
    finite_json_loads,
)
from .service import Service, _log

server = MCPServer(name="ipe-bindcraft", version="0.1.0")
_service: Service | None = None

_OPS = TypeAdapter(list[Operation])


def svc() -> Service:
    global _service
    if _service is None:
        _service = Service()
    return _service


def _err(exc: Exception) -> dict:
    if isinstance(exc, IbcError):
        return {"error": exc.to_dict()}
    if isinstance(exc, ValidationError):
        return {"error": {"code": "VALIDATION", "message": str(exc)[:800],
                          "details": {"errors": exc.errors()[:20]}}}
    return {"error": {"code": "INTERNAL", "message": str(exc)[:800], "details": {}}}


def _call(fn, *a, **kw) -> dict:
    """Run a service call; business failures -> ToolError -> isError=true.

    The ToolError message carries the JSON-serialized error payload so the
    client gets the stable ``code`` and ``details``, not just prose.
    """
    import json as _json

    from mcp.server.mcpserver.exceptions import ToolError

    try:
        return fn(*a, **kw)
    except Exception as exc:
        err = _err(exc)
        _log(f"error {err['error']['code']}: {err['error']['message'][:200]}")
        raise ToolError(_json.dumps(err["error"], ensure_ascii=False)) from exc


# ---------------------------------------------------------------------------

@server.tool(name="doctor", description="Environment check: Ipe tools, TeX engines, capabilities")
def doctor() -> dict:
    return _call(svc().doctor)


@server.tool(name="list_presets", description="List style / tex / template presets")
def list_presets(kind: str) -> dict:
    return _call(svc().list_presets, kind)


@server.tool(name="create_document",
             description="Create a new .ipe document (single page, embedded ibc style)")
def create_document(path: str, width_bp: float, height_bp: float,
                    style_id: str, tex_profile: str = "paper-serif") -> dict:
    return _call(svc().create_document, path, width_bp, height_bp,
                 style_id, tex_profile)


@server.tool(name="open_document",
             description="Open an existing .ipe file. backend='file' edits the "
                         "file on disk; backend='live' launches a bound Ipe GUI "
                         "window and edits its in-memory document via the "
                         "ipebindcraft ipelet (native undo items).")
def open_document(path: str, backend: str = "file",
                  bridge_session_id: str | None = None) -> dict:
    return _call(svc().open_document, path, backend, bridge_session_id)


@server.tool(name="close_document", description="Close a document session")
def close_document(document_id: str) -> dict:
    return _call(svc().close_document, document_id)


@server.tool(name="inspect_document",
             description="List managed objects, optionally with geometry")
def inspect_document(document_id: str, ids: list[str] | None = None,
                     include_geometry: bool = False) -> dict:
    return _call(svc().inspect_document, document_id, ids, include_geometry)


@server.tool(name="apply_operations",
             description="Atomic batch of typed operations with revision + request dedup")
def apply_operations(document_id: str, expected_revision: str,
                     request_id: str, operations: list[dict[str, Any]],
                     dry_run: bool = False) -> dict:
    def run():
        check_finite_recursive(operations)
        ops = _OPS.validate_python(operations)
        inp = ApplyOperationsInput(
            document_id=document_id, expected_revision=expected_revision,
            request_id=request_id, operations=ops, dry_run=dry_run)
        return svc().apply_operations(inp)
    return _call(run)


@server.tool(name="layout_objects",
             description="Align / distribute / grid objects (dry_run returns planned moves)")
def layout_objects(document_id: str, expected_revision: str, request_id: str,
                   ids: list[str], mode: str, options: dict | None = None,
                   dry_run: bool = True) -> dict:
    return _call(svc().layout_objects, document_id, ids, mode, options,
                 expected_revision, request_id, dry_run)


@server.tool(name="route_edges",
             description="Re-route edges to actual outline ports")
def route_edges(document_id: str, expected_revision: str, request_id: str,
                edge_ids: list[str], dry_run: bool = False) -> dict:
    return _call(svc().route_edges, document_id, edge_ids,
                 expected_revision, request_id, dry_run)


@server.tool(name="lint_figure",
             description="Check overflow, overlaps, dangling edges, off-page objects")
def lint_figure(document_id: str, revision: str | None = None,
                target_width_bp: float | None = None) -> dict:
    return _call(svc().lint_figure, document_id, revision, target_width_bp)


@server.tool(name="polish_figure",
             description="Safe auto-fixes (label recenter, grow node to label). dry_run first.")
def polish_figure(document_id: str, expected_revision: str, request_id: str,
                  fixes: list[str], dry_run: bool = True) -> dict:
    return _call(svc().polish_figure, document_id, fixes,
                 expected_revision, request_id, dry_run)


@server.tool(name="render_preview",
             description="Render current revision to PNG (image content). "
                         "Fails with PREVIEW_STALE if revision mismatch.")
def render_preview(document_id: str, revision: str, dpi: int = 150,
                   overlay: bool = False) -> list:
    from .export import render_preview_png

    sess = svc()._session(document_id)
    if revision != sess.revision:
        import json as _json
        from mcp.server.mcpserver.exceptions import ToolError
        raise ToolError(_json.dumps({
            "code": "PREVIEW_STALE",
            "message": f"preview requested at {revision}, current {sess.revision}",
            "details": {"current": sess.revision}}))
    png = render_preview_png(svc().tools(), sess.doc.serialize(), dpi=dpi)
    return [
        TextContent(type="text", text=f"preview rev {sess.revision} @ {dpi}dpi"),
        ImageContent(type="image", data=base64.b64encode(png).decode(),
                     mimeType="image/png"),
    ]


@server.tool(name="export_figure",
             description="Export pdf/svg/png into a generation directory with manifest")
def export_figure(document_id: str, revision: str, output_dir: str,
                  basename: str, formats: list[str], dpi: int = 300) -> dict:
    def run():
        from .export import export_figure as do_export
        sess = svc()._session(document_id)
        if revision != sess.revision:
            raise IbcError("PREVIEW_STALE",
                           f"export requested at {revision}, current {sess.revision}")
        return do_export(svc().tools(), sess.doc.serialize(), revision,
                         output_dir, basename, formats, dpi)
    return _call(run)


@server.tool(name="save_document", description="Persist the session document (optionally to a new path)")
def save_document(document_id: str, expected_revision: str, request_id: str,
                  path: str | None = None) -> dict:
    return _call(svc().save_document, document_id, expected_revision,
                 request_id, path)


@server.tool(name="get_request_status",
             description="Look up a request_id in the commit journal")
def get_request_status(document_id: str, request_id: str) -> dict:
    return _call(svc().get_request_status, document_id, request_id)


def main():
    _log("ipe-bindcraft MCP server starting (stdio)")
    anyio.run(server.run_stdio_async)


if __name__ == "__main__":
    main()
