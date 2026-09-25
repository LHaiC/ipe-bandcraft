"""ipe-bindcraft CLI — same service layer as the MCP server.

Every subcommand prints a JSON result on stdout; logs go to stderr.
Exit codes: 0 ok, 2 business error (structured JSON error on stdout),
3 usage error.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from .errors import IbcError, UsageError
from .schemas import Operation, check_finite_recursive, exported_schema_json
from .service import Service

_OPS = TypeAdapter(list[Operation])


def _out(obj) -> int:
    sys.stdout.write(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
    return 0


def _fail(exc: Exception) -> int:
    if isinstance(exc, UsageError):
        _out({"error": exc.to_dict()})
        return 3
    if isinstance(exc, IbcError):
        _out({"error": exc.to_dict()})
        return 2
    _out({"error": {"code": "INTERNAL", "message": str(exc), "details": {}}})
    return 2


def _load_ops(spec: str) -> list[Operation]:
    """ops payload: '-' stdin, @file, a bare file path, or inline JSON.

    The payload itself is a bare JSON array of operations; a wrapper object
    ``{"ops": [...]}`` / ``{"operations": [...]}`` is also accepted.
    """
    origin: str | None = None
    if spec == "-":
        raw = sys.stdin.read()
        origin = "stdin"
    elif spec.startswith("@"):
        origin = spec[1:]
        try:
            raw = Path(origin).read_text(encoding="utf-8")
        except OSError as exc:
            raise UsageError(f"cannot read ops file {origin!r}: {exc}") from exc
    elif Path(spec).is_file():
        # bare filename — what newcomers naturally type; used to be parsed as
        # a JSON literal and blow up with an opaque INTERNAL error.
        origin = spec
        raw = Path(spec).read_text(encoding="utf-8")
    else:
        raw = spec
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        hint = ""
        if origin is None and not raw.lstrip().startswith(("[", "{")):
            hint = (f"; {spec!r} is neither a file nor JSON; "
                    f"if you meant an ops file use @{spec}")
        raise UsageError(f"ops payload is not valid JSON: {exc}{hint}",
                         details={"source": origin or "argv"}) from exc
    if isinstance(data, dict):
        for key in ("ops", "operations"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            raise UsageError(
                'ops object must contain an "ops" array of operations '
                '(or pass a bare JSON array)',
                details={"source": origin or "argv"})
    try:
        check_finite_recursive(data)
        return _OPS.validate_python(data)
    except ValidationError as exc:
        raise UsageError(f"ops failed schema validation: {exc}",
                         details={"source": origin or "argv"}) from exc
    except ValueError as exc:
        raise UsageError(f"invalid ops payload: {exc}",
                         details={"source": origin or "argv"}) from exc


def _resolve_revision(info: dict, rev: str) -> str:
    """'latest'/'current' -> the revision observed when the doc was opened."""
    if rev.lower() in ("latest", "current"):
        return info["revision"]
    return rev


def _install_skill(repo: Path, dest: str, force: bool) -> dict:
    """Copy the bundled academic-figure skill into a consumer repo."""
    src = Path(__file__).resolve().parents[2] / "skills" / "academic-figure"
    if not src.is_dir():
        raise UsageError(
            "bundled skills/ directory not found (the installed wheel does not "
            "ship it); copy skills/academic-figure from an ipe-bandcraft "
            "source checkout instead")
    if not repo.is_dir():
        raise UsageError(f"target repo {str(repo)!r} is not a directory")
    roots = {"agents": repo / ".agents" / "skills" / "academic-figure",
             "devin": repo / ".devin" / "skills" / "academic-figure"}
    targets = [roots[d] for d in (["agents", "devin"] if dest == "both"
                                  else [dest])]
    written = []
    for t in targets:
        if t.exists() and not force:
            raise UsageError(f"{t} already exists; pass --force to overwrite")
        shutil.copytree(src, t, dirs_exist_ok=force)
        written.append(str(t))
    return {"installed": written, "skill": "academic-figure"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ipe-bindcraft",
                                description="Ipe MCP/CLI for academic paper figures")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="probe Ipe/TeX environment")
    sub.add_parser("schema", help="print the apply_operations JSON Schema")
    sub.add_parser("serve", help="run the MCP stdio server")
    sub.add_parser("install-ipelet",
                   help="install ipebindcraft.lua into ~/.ipe/ipelets")
    sk = sub.add_parser("install-skill",
                        help="install the academic-figure agent skill into a repo")
    sk.add_argument("repo", help="target repository root")
    sk.add_argument("--dest", choices=["agents", "devin", "both"],
                    default="agents",
                    help="skill dir convention: .agents/skills (default), "
                         ".devin/skills, or both")
    sk.add_argument("--force", action="store_true",
                    help="overwrite an existing installation")

    op = sub.add_parser("open", help="open a document (file or live backend)")
    op.add_argument("path")
    op.add_argument("--backend", choices=["file", "live"], default="file")

    st = sub.add_parser(
        "status", help="current revision, page size, objects, journal state")
    st.add_argument("path")

    c = sub.add_parser("create", help="create a new .ipe document")
    c.add_argument("path")
    c.add_argument("--width", type=float, default=595.276)
    c.add_argument("--height", type=float, default=841.89)
    c.add_argument("--style", default="paper-default")
    c.add_argument("--tex-profile", default="paper-serif")
    c.add_argument("--palette", default=None)
    c.add_argument("--template", default=None)

    o = sub.add_parser("inspect", help="open + list objects (with geometry)")
    o.add_argument("path")
    o.add_argument("--geometry", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="include per-object bounding boxes in API coords "
                        "(default; --no-geometry for ids/kinds only)")
    o.add_argument("--ids", nargs="*")

    a = sub.add_parser(
        "apply", help="apply an ops batch (JSON / @file / bare file / -)",
        epilog="""examples:
  ipe-bindcraft apply fig.ipe @ops.json --revision latest --request-id r1
  ipe-bindcraft apply fig.ipe ops.json --revision sha256:abc... --request-id r2 --lint --preview out.png
  ipe-bindcraft apply fig.ipe '[{"op":"objects.move_to","ids":["n1"],"x":100,"y":80}]' --revision latest --request-id r3

ops payload: a bare JSON array of operations; also accepts @file, a plain
file path, '-' for stdin, or {"ops": [...]}. All coordinates are API space:
origin top-left, +x right, +y down, units bp. objects.translate is relative;
objects.move_to / node.update.changes.box are absolute.""",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("path")
    a.add_argument("ops")
    a.add_argument("--revision", required=True,
                   help="expected revision, or 'latest' to use the file's "
                        "current revision")
    a.add_argument("--request-id", required=True)
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--lint", action="store_true",
                   help="embed a lint pass on the result figure")
    a.add_argument("--preview", metavar="PNG",
                   help="write a PNG of the post-apply figure")
    a.add_argument("--annotate", action="store_true",
                   help="draw the API-coord grid + object ids on --preview")
    a.add_argument("--grid-bp", type=float, default=50.0,
                   help="grid spacing for --annotate (bp)")

    r = sub.add_parser("route", help="reroute edges")
    r.add_argument("path")
    r.add_argument("edge_ids", nargs="+")
    r.add_argument("--revision", required=True,
                   help="expected revision, or 'latest'")
    r.add_argument("--request-id", required=True)

    l = sub.add_parser("layout", help="align/distribute/grid")
    l.add_argument("path")
    l.add_argument("ids", nargs="+")
    l.add_argument("--mode", choices=["align", "distribute", "grid"], required=True)
    l.add_argument("--options", default=None, help="JSON dict")
    l.add_argument("--revision", required=True,
                   help="expected revision, or 'latest'")
    l.add_argument("--request-id", required=True)
    l.add_argument("--apply", action="store_true", help="commit (default is dry-run)")

    li = sub.add_parser("lint", help="lint a document")
    li.add_argument("path")
    li.add_argument("--target-width", type=float)

    po = sub.add_parser("polish", help="safe-polish plan/apply")
    po.add_argument("path")
    po.add_argument("fixes", nargs="+")
    po.add_argument("--revision", required=True,
                    help="expected revision, or 'latest'")
    po.add_argument("--request-id", required=True)
    po.add_argument("--apply", action="store_true")

    pv = sub.add_parser("preview", help="render PNG preview")
    pv.add_argument("path")
    pv.add_argument("--dpi", type=int, default=150)
    pv.add_argument("--annotate", action="store_true",
                    help="draw the API-coord grid + object ids")
    pv.add_argument("--grid-bp", type=float, default=50.0,
                    help="grid spacing for --annotate (bp)")
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
        if args.cmd == "install-ipelet":
            from .backends.live_backend import install_ipelet
            return _out({"installed": str(install_ipelet())})
        if args.cmd == "install-skill":
            return _out(_install_skill(Path(args.repo), args.dest, args.force))
        if args.cmd == "open":
            return _out(svc.open_document(args.path, backend=args.backend))
        if args.cmd == "status":
            info = svc.open_document(args.path)
            info.update(svc.journal_summary(info["document_id"]))
            return _out(info)
        if args.cmd == "create":
            return _out(svc.create_document(args.path, args.width, args.height,
                                            args.style, args.tex_profile,
                                            palette=args.palette,
                                            template=args.template))
        if args.cmd == "inspect":
            info = svc.open_document(args.path)
            res = svc.inspect_document(info["document_id"], args.ids,
                                       args.geometry)
            return _out(res)
        if args.cmd == "apply":
            info = svc.open_document(args.path)
            from .schemas import ApplyOperationsInput
            inp = ApplyOperationsInput(
                document_id=info["document_id"],
                expected_revision=_resolve_revision(info, args.revision),
                request_id=args.request_id, operations=_load_ops(args.ops),
                dry_run=args.dry_run)
            res = svc.apply_operations(inp)
            if args.lint or args.preview:
                from .document import IpeDoc
                if args.lint:
                    from .quality import lint as _lint
                    from .snapshot import build_snapshot
                    # lint the post-apply candidate (also correct on dry-run)
                    cand = IpeDoc.parse(svc.preview_source_xml(info["document_id"]))
                    lres = _lint(build_snapshot(cand))
                    res["lint"] = {
                        "summary": lres["summary"],
                        "findings": [f for f in lres["findings"]
                                     if f["status"] != "pass"],
                        "coordinate_frame": lres.get("coordinate_frame"),
                    }
                if args.preview:
                    from .export import render_preview_png
                    png = render_preview_png(
                        svc.tools(), svc.preview_source_xml(info["document_id"]),
                        dpi=150, annotate=args.annotate, grid_bp=args.grid_bp)
                    Path(args.preview).write_bytes(png)
                    res["preview"] = {"written": args.preview,
                                      "bytes": len(png)}
            return _out(res)
        if args.cmd == "route":
            info = svc.open_document(args.path)
            return _out(svc.route_edges(
                info["document_id"], args.edge_ids,
                _resolve_revision(info, args.revision), args.request_id))
        if args.cmd == "layout":
            info = svc.open_document(args.path)
            opts = json.loads(args.options) if args.options else None
            return _out(svc.layout_objects(
                info["document_id"], args.ids, args.mode, opts,
                _resolve_revision(info, args.revision),
                args.request_id, not args.apply))
        if args.cmd == "lint":
            info = svc.open_document(args.path)
            return _out(svc.lint_figure(info["document_id"],
                                        target_width_bp=args.target_width))
        if args.cmd == "polish":
            info = svc.open_document(args.path)
            return _out(svc.polish_figure(
                info["document_id"], args.fixes,
                _resolve_revision(info, args.revision), args.request_id,
                not args.apply))
        if args.cmd == "preview":
            from .document import IpeDoc
            from .export import render_preview_png
            png = render_preview_png(svc.tools(), IpeDoc.load(args.path).serialize(),
                                     dpi=args.dpi, annotate=args.annotate,
                                     grid_bp=args.grid_bp)
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
