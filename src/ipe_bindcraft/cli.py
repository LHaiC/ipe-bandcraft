"""ipe-bindcraft CLI — same service layer as the MCP server.

Every subcommand prints a JSON result on stdout; logs go to stderr.
Exit codes: 0 ok, 2 business error (structured JSON error on stdout),
3 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .errors import IbcError
from .schemas import Operation, check_finite_recursive, exported_schema_json
from .service import Service
from pydantic import TypeAdapter

_OPS = TypeAdapter(list[Operation])


def _out(obj) -> int:
    sys.stdout.write(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
    return 0


def _fail(exc: Exception) -> int:
    if isinstance(exc, IbcError):
        return _out({"error": exc.to_dict()}) or 2
    return _out({"error": {"code": "INTERNAL", "message": str(exc), "details": {}}}) or 2


def _load_ops(spec: str) -> list[Operation]:
    """ops as JSON string, @file, or '-' for stdin."""
    if spec == "-":
        raw = sys.stdin.read()
    elif spec.startswith("@"):
        raw = Path(spec[1:]).read_text(encoding="utf-8")
    else:
        raw = spec
    data = json.loads(raw)
    check_finite_recursive(data)
    return _OPS.validate_python(data)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ipe-bindcraft",
                                description="Ipe MCP/CLI for academic paper figures")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="probe Ipe/TeX environment")
    sub.add_parser("schema", help="print the apply_operations JSON Schema")
    sub.add_parser("serve", help="run the MCP stdio server")

    c = sub.add_parser("create", help="create a new .ipe document")
    c.add_argument("path")
    c.add_argument("--width", type=float, default=595.276)
    c.add_argument("--height", type=float, default=841.89)
    c.add_argument("--style", default="paper-default")
    c.add_argument("--tex-profile", default="paper-serif")

    o = sub.add_parser("inspect", help="open + list objects")
    o.add_argument("path")
    o.add_argument("--geometry", action="store_true")
    o.add_argument("--ids", nargs="*")

    a = sub.add_parser("apply", help="apply an ops batch (JSON / @file / -)")
    a.add_argument("path")
    a.add_argument("ops")
    a.add_argument("--revision", required=True)
    a.add_argument("--request-id", required=True)
    a.add_argument("--dry-run", action="store_true")

    r = sub.add_parser("route", help="reroute edges")
    r.add_argument("path")
    r.add_argument("edge_ids", nargs="+")
    r.add_argument("--revision", required=True)
    r.add_argument("--request-id", required=True)

    l = sub.add_parser("layout", help="align/distribute/grid")
    l.add_argument("path")
    l.add_argument("ids", nargs="+")
    l.add_argument("--mode", choices=["align", "distribute", "grid"], required=True)
    l.add_argument("--options", default=None, help="JSON dict")
    l.add_argument("--revision", required=True)
    l.add_argument("--request-id", required=True)
    l.add_argument("--apply", action="store_true", help="commit (default is dry-run)")

    li = sub.add_parser("lint", help="lint a document")
    li.add_argument("path")
    li.add_argument("--target-width", type=float)

    po = sub.add_parser("polish", help="safe-polish plan/apply")
    po.add_argument("path")
    po.add_argument("fixes", nargs="+")
    po.add_argument("--revision", required=True)
    po.add_argument("--request-id", required=True)
    po.add_argument("--apply", action="store_true")

    pv = sub.add_parser("preview", help="render PNG preview")
    pv.add_argument("path")
    pv.add_argument("--dpi", type=int, default=150)
    pv.add_argument("-o", "--out", required=True)

    ex = sub.add_parser("export", help="export pdf/svg/png with manifest")
    ex.add_argument("path")
    ex.add_argument("--formats", nargs="+", default=["pdf"])
    ex.add_argument("--basename", default=None)
    ex.add_argument("-o", "--out-dir", required=True)
    ex.add_argument("--dpi", type=int, default=300)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    svc = Service()
    try:
        if args.cmd == "doctor":
            return _out(svc.doctor())
        if args.cmd == "schema":
            sys.stdout.write(exported_schema_json() + "\n")
            return 0
        if args.cmd == "serve":
            from .server import main as serve_main
            serve_main()
            return 0
        if args.cmd == "create":
            return _out(svc.create_document(args.path, args.width, args.height,
                                            args.style, args.tex_profile))
        if args.cmd == "inspect":
            info = svc.open_document(args.path)
            res = svc.inspect_document(info["document_id"], args.ids,
                                       args.geometry)
            return _out(res)
        if args.cmd == "apply":
            info = svc.open_document(args.path)
            from .schemas import ApplyOperationsInput
            inp = ApplyOperationsInput(
                document_id=info["document_id"], expected_revision=args.revision,
                request_id=args.request_id, operations=_load_ops(args.ops),
                dry_run=args.dry_run)
            return _out(svc.apply_operations(inp))
        if args.cmd == "route":
            info = svc.open_document(args.path)
            return _out(svc.route_edges(info["document_id"], args.edge_ids,
                                        args.revision, args.request_id))
        if args.cmd == "layout":
            info = svc.open_document(args.path)
            opts = json.loads(args.options) if args.options else None
            return _out(svc.layout_objects(info["document_id"], args.ids,
                                           args.mode, opts, args.revision,
                                           args.request_id, not args.apply))
        if args.cmd == "lint":
            info = svc.open_document(args.path)
            return _out(svc.lint_figure(info["document_id"],
                                        target_width_bp=args.target_width))
        if args.cmd == "polish":
            info = svc.open_document(args.path)
            return _out(svc.polish_figure(info["document_id"], args.fixes,
                                          args.revision, args.request_id,
                                          not args.apply))
        if args.cmd == "preview":
            from .document import IpeDoc
            from .export import render_preview_png
            png = render_preview_png(svc.tools(), IpeDoc.load(args.path).serialize(),
                                     dpi=args.dpi)
            Path(args.out).write_bytes(png)
            return _out({"written": args.out, "bytes": len(png)})
        if args.cmd == "export":
            from .document import IpeDoc
            from .export import export_figure
            base = args.basename or Path(args.path).stem
            m = export_figure(svc.tools(), IpeDoc.load(args.path).serialize(),
                              "cli", args.out_dir, base, args.formats, args.dpi)
            return _out(m)
        raise SystemExit(3)
    except Exception as exc:  # noqa: BLE001 — uniform JSON error surface
        return _fail(exc)


if __name__ == "__main__":
    raise SystemExit(main())
