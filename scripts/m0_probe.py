"""M0 compatibility probe for ipe-bindcraft.

Runs real Ipe/TeX/SDK checks on the target machine and prints a JSON report.
Nothing here is mocked: a check is "pass" only if the real tool produced the
expected artifact. Usage:

    .venv/Scripts/python.exe scripts/m0_probe.py [--out docs/capability-report-data.json]
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ipe_bindcraft.discovery import discover  # noqa: E402

SUBPROC_KW = dict(encoding="utf-8", errors="replace") if sys.platform == "win32" else {}
if sys.platform == "win32":
    SUBPROC_KW["creationflags"] = subprocess.CREATE_NO_WINDOW


def run(argv: list[str], cwd: Path | None = None, timeout: float = 120.0):
    try:
        p = subprocess.run(
            argv, capture_output=True, timeout=timeout, cwd=cwd, **SUBPROC_KW
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return -9, "timeout"
    except OSError as exc:
        return -1, f"os error: {exc}"


def b64url_json(obj) -> str:
    raw = json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return "ibc1:" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def make_doc(*, custom: bool = False, texts: bool = True) -> str:
    """Minimal single-page document exercising the features we depend on."""
    page_w, page_h = 252, 190
    custom_attr = f' custom="{b64url_json({"schema": 1, "id": "probe_rect", "kind": "node"})}"' if custom else ""
    meta_layer = ""
    if custom:
        meta_layer = (
            f'<layer name="ibc-meta" edit="no" snap="never" '
            f'data="{b64url_json({"schema": 1, "style_id": "paper-default"})}"/>'
        )
    text_objs = ""
    if texts:
        text_objs = f"""
<text transformations="translations" pos="10 170" stroke="black" type="label"
      valign="baseline" halign="left" size="8">plain\_underscore \&amp; percent\% end</text>
<text transformations="translations" pos="10 150" stroke="black" type="label"
      valign="baseline" halign="left" size="8">$\\Delta t$ math</text>
"""
    return f"""<?xml version="1.0"?>
<ipe version="70218" creator="ipe-bindcraft-m0">
<ipestyle name="ibc-probe">
<layout paper="{page_w} {page_h}" origin="0 0" frame="{page_w} {page_h}" crop="yes"/>
</ipestyle>
<page>
<layer name="alpha"/>
{meta_layer}<view layers="alpha" active="alpha"/>
<path stroke="black" pen="0.6"{custom_attr}>
40 140 m
120 140 l
120 170 l
40 170 l
h
</path>
<path stroke="black" pen="0.6">
160 140 m
240 140 l
240 170 l
160 170 l
h
</path>
<path stroke="black" pen="0.6" arrow="normal/normal">
120 155 m
160 155 l
</path>
<path stroke="black" pen="0.6" fill="none">
60 80 m
60 40 l
110 40 l
110 80 l
h
</path>
<path stroke="black" pen="0.6">
0.35 0 0 0.35 200 60 e
</path>
{text_objs}</page>
</ipe>
"""


def png_size(path: Path) -> tuple[int, int] | None:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def pdf_boxes(path: Path) -> dict:
    from pypdf import PdfReader

    r = PdfReader(str(path))
    box = r.pages[0].mediabox
    out = {"mediabox": [float(box.left), float(box.bottom), float(box.right), float(box.top)]}
    cb = r.pages[0].cropbox
    out["cropbox"] = [float(cb.left), float(cb.bottom), float(cb.right), float(cb.top)]
    fonts = []
    res = r.pages[0].get("/Resources")
    if res and "/Font" in res:
        for f in res["/Font"].values():
            fo = f.get_object()
            fonts.append(str(fo.get("/BaseFont")))
    out["fonts"] = fonts
    return out


def main() -> int:
    report: dict = {"checks": {}, "env": {}}
    disc = discover()
    report["env"] = disc.to_json()
    checks = report["checks"]

    if not disc.ipe_root:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        print("IPE NOT FOUND — all Ipe checks skipped", file=sys.stderr)
        return 2

    tools = {k: Path(v.path) for k, v in disc.tools.items() if v.path}

    work = Path(tempfile.mkdtemp(prefix="ibc-m0-"))
    report["workdir"] = str(work)

    # --- check: ipetoipe XML round-trip ------------------------------------
    src = work / "probe.ipe"
    src.write_text(make_doc(custom=True), encoding="utf-8")
    rt = work / "probe_rt.ipe"
    rc, out = run([str(tools["ipetoipe"]), "-xml", str(src), str(rt)])
    checks["xml_roundtrip"] = {
        "pass": rc == 0 and rt.is_file(),
        "rc": rc,
        "output": out.strip()[:2000],
    }

    # --- check: custom attribute + layer data survive Ipe save -------------
    surv = {"custom_attr": False, "layer_data": False, "detail": ""}
    if rt.is_file():
        content = rt.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'custom="([^"]+)"', content)
        if m:
            val = m.group(1)
            try:
                pad = "=" * (-len(val[5:]) % 4)
                decoded = json.loads(base64.urlsafe_b64decode(val[5:] + pad))
                surv["custom_attr"] = decoded.get("id") == "probe_rect"
                surv["custom_value"] = val
                surv["decoded"] = decoded
            except Exception as exc:  # noqa: BLE001
                surv["detail"] = f"custom decode failed: {exc}"
        else:
            surv["detail"] = "no custom attribute after ipetoipe round-trip"
        m2 = re.search(r'<layer name="ibc-meta"[^>]*data="([^"]+)"', content)
        if m2:
            val = m2.group(1)
            try:
                pad = "=" * (-len(val[5:]) % 4)
                decoded = json.loads(base64.urlsafe_b64decode(val[5:] + pad))
                surv["layer_data"] = decoded.get("style_id") == "paper-default"
            except Exception as exc:  # noqa: BLE001
                surv["detail"] += f" | layer data decode failed: {exc}"
    checks["custom_roundtrip"] = {
        "pass": surv["custom_attr"] and surv["layer_data"],
        **surv,
    }

    # --- check: LaTeX text measurement ---------------------------------------
    # NOTE: `ipetoipe -xml -runlatex` in Ipe 7.2.29 writes *PDF*, not XML with
    # text dimensions (verified against src/ipetoipe/ipetoipe.cpp @v7.2.29:
    # the Xml+runLatex branch calls topdf()). The working path is ipescript:
    #   ipescript measure in.ipe out.ipe   (doc:runLatex() + doc:save()).
    # First record the ipetoipe behavior as evidence, then use ipescript.
    base = rt if rt.is_file() else src
    meas_ipetoipe = work / "probe_ipetoipe_runlatex.out"
    rc0, out0 = run(
        [str(tools["ipetoipe"]), "-xml", "-runlatex", str(base), str(meas_ipetoipe)],
        timeout=300,
    )
    ipetoipe_is_pdf = meas_ipetoipe.is_file() and meas_ipetoipe.read_bytes()[:4] == b"%PDF"

    lua_src = Path(__file__).resolve().parent.parent / "src" / "ipe_bindcraft" / "lua"
    meas = work / "probe_measured.ipe"
    rc, out = run(
        [str(tools["ipescript"]), "measure", str(base), str(meas)],
        cwd=lua_src,
        timeout=300,
    )
    dims = {}
    if meas.is_file() and meas.read_bytes()[:5] != b"%PDF-":
        content = meas.read_text(encoding="utf-8", errors="replace")
        dims = {
            "width_attrs": re.findall(r'width="([0-9.]+)"', content),
            "height_attrs": re.findall(r'height="([0-9.]+)"', content),
            "depth_attrs": re.findall(r'depth="([0-9.]+)"', content),
        }
    checks["latex_measure"] = {
        "pass": rc == 0 and meas.is_file() and bool(dims.get("width_attrs")),
        "rc": rc,
        "output": out.strip()[:3000],
        "ipetoipe_xml_runlatex_produced_pdf": ipetoipe_is_pdf,
        "spec_deviation": (
            "SPEC section 9.2 template `ipetoipe -xml -runlatex` emits PDF in "
            "Ipe 7.2.29; measurement uses `ipescript measure` instead."
        ),
        **dims,
    }

    # --- check: internal PDF + PNG + SVG + publication PDF ------------------
    pdf_internal = work / "internal.pdf"
    rc, out = run([str(tools["ipetoipe"]), "-pdf", str(meas if meas.is_file() else src), str(pdf_internal)], timeout=300)
    checks["internal_pdf"] = {"pass": rc == 0 and pdf_internal.is_file(), "rc": rc, "output": out.strip()[:1000]}

    png = work / "preview.png"
    rc, out = run(
        [str(tools["iperender"]), "-png", "-page", "1", "-view", "1", "-resolution", "150",
         "-nocrop", str(pdf_internal), str(png)]
    )
    px = png_size(png) if png.is_file() else None
    expected = (round(252 * 150 / 72), round(190 * 150 / 72))
    checks["png_render"] = {
        "pass": rc == 0 and px is not None and abs(px[0] - expected[0]) <= 2 and abs(px[1] - expected[1]) <= 2,
        "rc": rc,
        "pixels": px,
        "expected": expected,
        "output": out.strip()[:1000],
    }

    svg = work / "figure.svg"
    rc, out = run(
        [str(tools["iperender"]), "-svg", "-page", "1", "-view", "1", "-nocrop", str(pdf_internal), str(svg)]
    )
    svg_info = {}
    if svg.is_file():
        head = svg.read_text(encoding="utf-8", errors="replace")[:4000]
        m = re.search(r'<svg[^>]*', head)
        svg_info["svg_tag"] = m.group(0)[:300] if m else None
    checks["svg_export"] = {"pass": rc == 0 and svg.is_file(), "rc": rc, "output": out.strip()[:1000], **svg_info}

    pdf_pub = work / "figure.pdf"
    rc, out = run(
        [str(tools["ipetoipe"]), "-pdf", "-export", "-view", "1-1", str(pdf_internal), str(pdf_pub)]
    )
    boxes = pdf_boxes(pdf_pub) if pdf_pub.is_file() else {}
    vis_ok = False
    if boxes:
        mb = boxes["mediabox"]
        vis_ok = abs(mb[2] - mb[0] - 252) < 0.5 and abs(mb[3] - mb[1] - 190) < 0.5
    checks["publication_pdf"] = {
        "pass": rc == 0 and pdf_pub.is_file() and vis_ok,
        "rc": rc,
        "visible_box_ok": vis_ok,
        "output": out.strip()[:1000],
        **(boxes or {}),
    }

    # --- check: paths with spaces and non-ASCII ------------------------------
    # Space-only paths must work. Non-ASCII argv paths are probed separately:
    # if the installed Ipe build mishandles them we record a limitation and
    # verify the ASCII-staging workaround instead of claiming support.
    space_dir = work / "dir with space"
    space_dir.mkdir(exist_ok=True)
    sp = space_dir / "a b.ipe"
    sp.write_text(make_doc(), encoding="utf-8")
    out_space = space_dir / "out b.ipe"
    rc, out = run([str(tools["ipetoipe"]), "-xml", str(sp), str(out_space)])
    checks["space_paths"] = {"pass": rc == 0 and out_space.is_file(), "rc": rc, "output": out.strip()[:500]}

    zh_dir = work / "子目录"
    zh_dir.mkdir(exist_ok=True)
    zp = zh_dir / "图.ipe"
    zp.write_text(make_doc(), encoding="utf-8")
    out_zh = zh_dir / "out.ipe"
    rc, out = run([str(tools["ipetoipe"]), "-xml", str(zp), str(out_zh)])
    nonascii_ok = rc == 0 and out_zh.is_file()
    checks["nonascii_paths"] = {
        "pass": nonascii_ok,
        "severity": "limitation",  # real limitation; workaround verified separately
        "rc": rc,
        "output": out.strip()[:500],
        "limitation": None if nonascii_ok else (
            "Ipe CLI cannot open non-ASCII argv paths on this system "
            "(ANSI codepage mismatch); service stages files in ASCII temp dirs."
        ),
    }

    # workaround A: relative ASCII filename with cwd inside the non-ASCII dir
    pure = zh_dir / "pure.ipe"
    pure.write_bytes(zp.read_bytes())
    rc, out = run([str(tools["ipetoipe"]), "-xml", "pure.ipe", "rel_out.ipe"], cwd=zh_dir)
    checks["relative_ascii_cwd_workaround"] = {
        "pass": rc == 0 and (zh_dir / "rel_out.ipe").is_file(),
        "rc": rc,
        "output": out.strip()[:500],
    }

    # workaround B: stage the same bytes at an ASCII path
    staging = work / "ascii-staging"
    staging.mkdir(exist_ok=True)
    staged_in = staging / "in.ipe"
    staged_in.write_bytes(zp.read_bytes())
    staged_out = staging / "out.ipe"
    rc, out = run([str(tools["ipetoipe"]), "-xml", str(staged_in), str(staged_out)])
    checks["ascii_staging_workaround"] = {
        "pass": rc == 0 and staged_out.is_file(),
        "rc": rc,
        "output": out.strip()[:500],
    }

    # --- check: MCP SDK v2 smoke (real stdio client) --------------------------
    try:
        import importlib.metadata

        mcp_ver = importlib.metadata.version("mcp")
    except Exception:  # noqa: BLE001
        mcp_ver = None
    client_py = Path(__file__).with_name("m0_smoke_client.py")
    smoke = None
    rc = -1
    out = ""
    try:
        p = subprocess.run(
            [sys.executable, str(client_py)],
            capture_output=True,
            timeout=60,
            **SUBPROC_KW,
        )
        rc = p.returncode
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        for line in reversed((p.stdout or "").strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                smoke = json.loads(line)
                break
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        out = f"error: {exc}"
    checks["mcp_sdk_stdio"] = {
        "pass": rc == 0 and smoke is not None,
        "rc": rc,
        "sdk_version": mcp_ver,
        "detail": smoke,
        "output": out[:1500],
    }

    report["summary"] = {
        "passed": sum(1 for c in checks.values() if c.get("pass")),
        "total": len(checks),
        "all_pass": all(c.get("pass") for c in checks.values()),
        "blocking_failures": [
            k for k, c in checks.items()
            if not c.get("pass") and c.get("severity") != "limitation"
        ],
        "limitations": [
            k for k, c in checks.items()
            if not c.get("pass") and c.get("severity") == "limitation"
        ],
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))

    if "--out" in sys.argv:
        out_path = Path(sys.argv[sys.argv.index("--out") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if report["summary"]["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
