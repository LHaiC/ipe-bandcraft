"""Export and preview via the real Ipe CLI tools.

- Exports go to <output_dir>/<basename>-r<NNN>/ where NNN is a generation
  counter derived from the output dir — a failed export never clobbers a
  previous good one.
- Ipe CLI tools can't take non-ASCII argv paths on this system (M0 finding),
  so all renders are staged in an ASCII temp dir and the finished files are
  then copied to the caller's output directory.
- A manifest.json records the source revision, per-file sha256, and tool
  versions; ``verify`` recomputes the hashes.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import discovery
from .errors import IbcError

SUBPROC_KW: dict = dict(encoding="utf-8", errors="replace")
if sys.platform == "win32":
    SUBPROC_KW["creationflags"] = subprocess.CREATE_NO_WINDOW

TIMEOUT_S = 120.0


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True,
                              timeout=TIMEOUT_S, **SUBPROC_KW)
    except subprocess.TimeoutExpired as exc:
        raise IbcError("INTERNAL", f"export tool timed out: {cmd[0]}",
                       details={"cmd": cmd}) from exc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _next_generation(output_dir: Path, basename: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for child in output_dir.glob(f"{basename}-r*"):
        try:
            n = max(n, int(child.name.rsplit("-r", 1)[1]))
        except (ValueError, IndexError):
            continue
    return output_dir / f"{basename}-r{n + 1:03d}"


def export_figure(tools: discovery.Discovery, ipe_xml: bytes, revision: str,
                  output_dir: str, basename: str,
                  formats: list[str], dpi: int = 300) -> dict:
    """Render `formats` from the given XML bytes. Returns manifest dict."""
    if not tools.iperender:
        raise IbcError("BACKEND_CAPABILITY_UNAVAILABLE",
                       "iperender not found; cannot export", details=tools.report())
    gen_dir = _next_generation(Path(output_dir), basename)

    with tempfile.TemporaryDirectory(prefix="ibc-export-") as tmp:
        stage = Path(tmp)
        src = stage / "src.ipe"
        src.write_bytes(ipe_xml)
        produced: dict[str, Path] = {}
        for fmt in formats:
            dst = stage / f"{basename}.{fmt}"
            if fmt == "pdf":
                # ipetoipe -pdf keeps text as real PDF text
                if not tools.ipetoipe:
                    raise IbcError("BACKEND_CAPABILITY_UNAVAILABLE",
                                   "ipetoipe not found; PDF export unavailable")
                proc = _run([str(tools.ipetoipe), "-pdf", str(src), str(dst)],
                            cwd=stage)
            elif fmt == "svg":
                proc = _run([str(tools.iperender), "-svg", str(src), str(dst)],
                            cwd=stage)
            elif fmt == "png":
                proc = _run([str(tools.iperender), "-png", "-resolution",
                             str(dpi), str(src), str(dst)], cwd=stage)
            else:
                raise IbcError("VALIDATION", f"unsupported format {fmt!r}")
            if proc.returncode != 0 or not dst.is_file():
                raise IbcError(
                    "INTERNAL",
                    f"{fmt} export failed (rc={proc.returncode}): "
                    f"{(proc.stderr or proc.stdout or '')[-800:]}",
                    details={"format": fmt, "rc": proc.returncode},
                )
            produced[fmt] = dst

        gen_dir.mkdir(parents=True, exist_ok=False)
        files = {}
        for fmt, staged in produced.items():
            target = gen_dir / staged.name
            shutil.copy2(staged, target)
            files[fmt] = {
                "path": str(target),
                "sha256": _sha256(target),
                "bytes": target.stat().st_size,
            }

    manifest = {
        "basename": basename,
        "revision": revision,
        "generated_at": time.time(),
        "generation_dir": str(gen_dir),
        "files": files,
        "tools": {"iperender": str(tools.iperender or ""),
                  "ipetoipe": str(tools.ipetoipe or ""),
                  "ipe_version": tools.ipe_version},
    }
    (gen_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["manifest_path"] = str(gen_dir / "manifest.json")
    return manifest


def verify_manifest(manifest_path: str) -> dict:
    m = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    bad = {}
    for fmt, rec in m.get("files", {}).items():
        p = Path(rec["path"])
        if not p.is_file() or _sha256(p) != rec["sha256"]:
            bad[fmt] = str(p)
    return {"manifest": manifest_path, "ok": not bad, "mismatched": bad}


def pdf_bbox_report(pdf_path: Path) -> dict:
    """Visible-box + font report for a PDF page via pypdf.

    Ipe puts text inside Form XObjects, so fonts are collected recursively
    through the resource tree.
    """
    from pypdf import PdfReader
    from pypdf.generic import IndirectObject

    r = PdfReader(str(pdf_path))
    page = r.pages[0]
    mb = page.mediabox
    cb = page.cropbox
    fonts: set[str] = set()
    seen: set[int] = set()

    def walk(res):
        if res is None:
            return
        for f in (res.get("/Font", {}) or {}).values():
            fonts.add(str(f.get("/BaseFont", "?")))
        for x in (res.get("/XObject", {}) or {}).values():
            try:
                obj = x.get_object() if isinstance(x, IndirectObject) else x
            except Exception:
                continue
            key = id(obj)
            if key in seen:
                continue
            seen.add(key)
            walk(obj.get("/Resources"))

    walk(page.get("/Resources"))
    return {
        "mediabox": [float(mb.left), float(mb.bottom), float(mb.right), float(mb.top)],
        "cropbox": [float(cb.left), float(cb.bottom), float(cb.right), float(cb.top)],
        "fonts": sorted(fonts),
    }


def png_dimensions(png_path: Path) -> tuple[int, int]:
    import struct

    data = png_path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise IbcError("INTERNAL", "not a PNG file")
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def render_preview_png(tools: discovery.Discovery, ipe_xml: bytes,
                       dpi: int = 150) -> bytes:
    """Render the doc to PNG bytes for MCP image content."""
    if not tools.iperender:
        raise IbcError("BACKEND_CAPABILITY_UNAVAILABLE", "iperender not found")
    with tempfile.TemporaryDirectory(prefix="ibc-preview-") as tmp:
        stage = Path(tmp)
        src = stage / "src.ipe"
        dst = stage / "preview.png"
        src.write_bytes(ipe_xml)
        proc = _run([str(tools.iperender), "-png", "-resolution", str(dpi),
                     str(src), str(dst)], cwd=stage)
        if proc.returncode != 0 or not dst.is_file():
            raise IbcError("INTERNAL",
                           f"preview render failed: {(proc.stderr or '')[-500:]}")
        return dst.read_bytes()
