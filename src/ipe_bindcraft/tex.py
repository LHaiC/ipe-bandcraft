"""LaTeX measurement via the real Ipe/TeX toolchain.

`ipetoipe -xml -runlatex` emits PDF in Ipe 7.2.29 (see capability report), so
measurement runs through `ipescript` + the bundled `measure.lua`
(`doc:runLatex()` then `doc:save()` -> XML carrying width/height/depth).

Every job gets an isolated ASCII working directory: the installed Ipe CLI
cannot open non-ASCII argv paths on this system (verified limitation), and
per-job dirs also isolate Ipe's LaTeX runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from .errors import IbcError

SUBPROC_KW: dict = dict(encoding="utf-8", errors="replace")
if sys.platform == "win32":
    SUBPROC_KW["creationflags"] = subprocess.CREATE_NO_WINDOW

_LUA_DIR = Path(__file__).resolve().parent / "lua"


class TexError(IbcError):
    pass


def _ascii_safe(path: Path) -> bool:
    try:
        str(path).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


class TexMeasurer:
    """Runs ipescript measure jobs; each job in a fresh ASCII work dir."""

    def __init__(self, ipescript: str, work_root: Path | None = None,
                 timeout: float = 180.0, tex_engine: str = "pdflatex"):
        self.ipescript = ipescript
        self.timeout = timeout
        self.tex_engine = tex_engine
        self._work_root = work_root
        self._lock = threading.Lock()
        self._counter = 0

    def _job_dir(self) -> Path:
        with self._lock:
            self._counter += 1
            n = self._counter
        base = self._work_root or Path(tempfile.gettempdir()) / "ipe-bindcraft"
        d = base / f"texjob-{os.getpid()}-{n}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def measure_doc(self, ipe_xml: bytes) -> bytes:
        """Run LaTeX on the doc; returns measured XML bytes."""
        job = self._job_dir()
        src = job / "in.ipe"
        dst = job / "out.ipe"
        src.write_bytes(ipe_xml)
        env = dict(os.environ)
        env["IPELATEXDIR"] = str(job / "latex")
        env["IPETEXENGINE"] = self.tex_engine
        try:
            proc = subprocess.run(
                [self.ipescript, "measure", str(src), str(dst)],
                cwd=_LUA_DIR,
                capture_output=True,
                timeout=self.timeout,
                env=env,
                **SUBPROC_KW,
            )
        except subprocess.TimeoutExpired as exc:
            raise TexError(
                "LATEX_FAILED", f"LaTeX measurement timed out after {self.timeout}s",
                details={"stage": "runlatex"},
            ) from exc
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        if proc.returncode != 0 or not dst.is_file():
            # surface the real Ipe/LaTeX diagnostic
            raise TexError(
                "LATEX_FAILED",
                f"LaTeX run failed: {out[-1500:]}",
                details={"rc": proc.returncode, "log": out[-3000:]},
            )
        if "ibc-measure-fail" in out:
            raise TexError("LATEX_FAILED", f"LaTeX errors: {out[-1500:]}",
                           details={"log": out[-3000:]})
        data = dst.read_bytes()
        if data[:5] == b"%PDF-":
            raise TexError("INTERNAL", "measure output was PDF, not XML")
        return data


def measure_cache_key(doc_hash: str, tex_profile: dict, engine: str) -> str:
    """Cache key includes doc content + engine + preamble; disableable."""
    h = hashlib.sha256()
    h.update(doc_hash.encode())
    h.update(engine.encode())
    h.update(json.dumps(tex_profile, sort_keys=True).encode())
    return h.hexdigest()[:24]
