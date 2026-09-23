"""Dependency discovery for ipe-bindcraft.

Finds the real Ipe / TeX executables on the target machine. Discovery order
(per SPEC section 12): explicit config -> project env vars -> PATH ->
well-known portable install directories. We never assume a fixed install
path such as C:\\Program Files\\Ipe.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Executables we need from an Ipe installation.
IPE_TOOLS = ("ipe", "ipetoipe", "iperender", "ipescript")
# TeX engines we probe for (pdftex is required for the default profile).
TEX_TOOLS = ("pdflatex", "xelatex", "lualatex")

EXE = ".exe" if sys.platform == "win32" else ""


@dataclass
class ToolInfo:
    name: str
    path: str | None = None
    version: str | None = None
    source: str | None = None  # how it was found: config/env/PATH/portable
    error: str | None = None

    @property
    def found(self) -> bool:
        return self.path is not None


@dataclass
class Discovery:
    ipe_root: Path | None = None
    tools: dict[str, ToolInfo] = field(default_factory=dict)
    tex: dict[str, ToolInfo] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        def ti(t: ToolInfo) -> dict:
            return {
                "path": t.path,
                "version": t.version,
                "source": t.source,
                "error": t.error,
                "found": t.found,
            }

        return {
            "ipe_root": str(self.ipe_root) if self.ipe_root else None,
            "tools": {k: ti(v) for k, v in self.tools.items()},
            "tex": {k: ti(v) for k, v in self.tex.items()},
            "notes": list(self.notes),
        }

    # convenience accessors -------------------------------------------------
    def _tool(self, name: str) -> Path | None:
        t = self.tools.get(name)
        return Path(t.path) if t and t.path else None

    @property
    def ipe(self) -> Path | None:
        return self._tool("ipe")

    @property
    def ipetoipe(self) -> Path | None:
        return self._tool("ipetoipe")

    @property
    def iperender(self) -> Path | None:
        return self._tool("iperender")

    @property
    def ipescript(self) -> Path | None:
        return self._tool("ipescript")

    @property
    def ipe_version(self) -> str | None:
        for t in self.tools.values():
            if t.version:
                return t.version
        return None

    def report(self) -> dict:
        payload = self.to_json()
        payload["python"] = sys.version.split()[0]
        try:
            import mcp  # noqa: F401
            payload["mcp_sdk"] = getattr(mcp, "__version__", "installed")
        except ImportError:
            payload["mcp_sdk"] = None
        try:
            import pydantic
            payload["pydantic"] = pydantic.VERSION
        except ImportError:
            payload["pydantic"] = None
        payload["capabilities"] = {
            "file_backend": bool(self.ipetoipe and self.iperender),
            "latex_measure": bool(self.ipescript and self.tex.get("pdflatex")
                                  and self.tex["pdflatex"].found),
            "export_pdf": bool(self.ipetoipe),
            "export_svg": bool(self.iperender),
            "export_png": bool(self.iperender),
            "live_backend": False,  # M3 pending
            "nonascii_paths": False,  # verified Ipe 7.2.29 limitation (M0)
        }
        return payload


def _run_version(path: Path, args: list[str], timeout: float = 15.0) -> str | None:
    """Run a tool to obtain a version string; never raises."""
    try:
        proc = subprocess.run(
            [str(path), *args],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return out.strip() or None
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"error: {exc}"


def _extract_version(text: str | None) -> str | None:
    if not text:
        return None
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", text)
    return m.group(1) if m else text.splitlines()[0][:80]


def _candidate_ipe_dirs() -> list[tuple[Path, str]]:
    """Yield (dir, source) candidates for an Ipe bin directory."""
    cands: list[tuple[Path, str]] = []
    # 1. explicit env var pointing at the bin dir or install root
    for var in ("IPE_BINDCRAFT_IPE_BIN", "IPE_BINDCRAFT_IPE_ROOT", "IPEBIN"):
        val = os.environ.get(var)
        if val:
            cands.append((Path(val), f"env:{var}"))
    # 2. PATH
    for name in IPE_TOOLS:
        hit = shutil.which(name)
        if hit:
            cands.append((Path(hit).parent, "PATH"))
            break
    # 3. well-known locations: winget package dir, Program Files, ~\ipe*
    local = os.environ.get("LOCALAPPDATA", "")
    home = Path.home()
    winget_pkgs = Path(local) / "Microsoft" / "WinGet" / "Packages" if local else None
    roots: list[Path] = []
    if winget_pkgs and winget_pkgs.is_dir():
        roots.extend(p for p in winget_pkgs.iterdir() if "ipe" in p.name.lower())
    for base in (Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
                 Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
                 home):
        if base.is_dir():
            roots.extend(p for p in base.iterdir() if p.name.lower().startswith("ipe"))
    for root in roots:
        # winget: <pkg>/ipe-<ver>/bin ; plain: <dir>/bin or <dir> itself
        for cand in (root, *sorted(root.glob("ipe-*/"))):
            for b in (cand / "bin", cand):
                if b.is_dir():
                    cands.append((b, f"portable:{root}"))
    return cands


def _probe_ipe_dir(bin_dir: Path) -> dict[str, Path] | None:
    found = {}
    for name in IPE_TOOLS:
        exe = bin_dir / f"{name}{EXE}"
        if exe.is_file():
            found[name] = exe
    # ipe.exe alone is not enough; we need the CLI tools
    return found if "ipetoipe" in found and "iperender" in found else None


def _ipe_version(bin_dir: Path) -> str | None:
    # readme.txt sits one level above bin/ and starts with "Ipe x.y.z"
    for cand in (bin_dir.parent / "readme.txt", bin_dir.parent / "news.txt"):
        try:
            head = cand.read_text(errors="replace")[:2000]
        except OSError:
            continue
        m = re.search(r"Ipe\s+(\d+\.\d+\.\d+)", head)
        if m:
            return m.group(1)
    return None


def discover(config_ipe_root: str | None = None) -> Discovery:
    """Discover Ipe and TeX. Returns a Discovery; nothing here raises."""
    disc = Discovery()

    candidates: list[tuple[Path, str]] = []
    if config_ipe_root:
        candidates.append((Path(config_ipe_root), "config"))
    candidates.extend(_candidate_ipe_dirs())

    seen: set[Path] = set()
    for cand, source in candidates:
        cand = cand.resolve()
        if cand in seen or not cand.is_dir():
            continue
        seen.add(cand)
        tools = _probe_ipe_dir(cand)
        if tools is None:
            continue
        # prefer first hit that has all four tools
        disc.ipe_root = cand
        version = _ipe_version(cand)
        for name in IPE_TOOLS:
            exe = tools.get(name)
            disc.tools[name] = ToolInfo(
                name=name,
                path=str(exe) if exe else None,
                version=version,
                source=source,
                error=None if exe else "not found in selected Ipe bin dir",
            )
        break

    if disc.ipe_root is None:
        for name in IPE_TOOLS:
            disc.tools[name] = ToolInfo(name=name, error="no Ipe installation found")
        disc.notes.append(
            "Ipe not found. Set IPE_BINDCRAFT_IPE_BIN to the Ipe bin directory "
            "or install Ipe (e.g. 'winget install OtfriedCheong.Ipe')."
        )

    for name in TEX_TOOLS:
        hit = shutil.which(name)
        if hit:
            out = _run_version(Path(hit), ["--version"])
            disc.tex[name] = ToolInfo(name=name, path=hit, version=_extract_version(out), source="PATH")
        else:
            disc.tex[name] = ToolInfo(name=name, error="not found on PATH")
    if not disc.tex.get("pdflatex", ToolInfo("pdflatex")).found:
        disc.notes.append("pdflatex not found; LaTeX measurement will be unavailable.")

    return disc


def doctor_json(config_ipe_root: str | None = None) -> str:
    disc = discover(config_ipe_root)
    payload = disc.to_json()
    payload["python"] = sys.version.split()[0]
    try:
        import mcp  # noqa: F401

        payload["mcp_sdk"] = getattr(mcp, "__version__", "installed")
    except ImportError:
        payload["mcp_sdk"] = None
    try:
        import pydantic

        payload["pydantic"] = pydantic.VERSION
    except ImportError:
        payload["pydantic"] = None
    return json.dumps(payload, indent=2, ensure_ascii=False)
