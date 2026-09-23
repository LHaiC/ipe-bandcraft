"""Live backend: the bound Ipe GUI's in-memory document is authoritative.

Bridge contract (see ipelet/ipebindcraft.lua):
- A session directory holds `session.txt`, `status.txt`, `baseline.ipepage`
  and numbered request dirs `req-<n>/` with `meta.txt` + `candidate.ipepage`.
- The ipelet polls the dir (Timer, 250 ms) or applies on the "Apply pending"
  menu action; each request gets `resp.txt` whose first line is one of:
    ok | fail <detail> | conflict <detail> | stale-epoch <detail>
- `apply` replaces the whole page through model:register -> a single native
  undo item ("ipe-bindcraft apply") in the GUI's undo stack.
- `undo`/`redo` invoke the model's native actions; `status` snapshots the
  current page to `req-<n>/page.ipepage` without touching the dirty flag.
- If the page changed in the GUI since the baseline, `apply` answers
  `conflict`; the caller must fetch the authoritative page (status), adopt it
  as the new revision, and surface REVISION_CONFLICT to the client.

Errors:
- No session dir / no response within the deadline -> BRIDGE_UNAVAILABLE or
  GUI_BUSY respectively.
- A response that never arrives after the GUI may have consumed the request
  is reported as COMMIT_STATUS_UNKNOWN (we cannot know whether it applied).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from ..errors import IbcError

POLL_INTERVAL_S = 0.05
APPLY_TIMEOUT_S = 30.0
START_TIMEOUT_S = 20.0
RESP_STATUSES = {"ok", "fail", "conflict", "stale-epoch"}


def ipelet_install_dir() -> Path:
    """User ipelet dir (main.lua: %USERPROFILE%\\Ipelets on Windows,
    ~/.ipe/ipelets elsewhere)."""
    if os.name == "nt":
        home = Path(os.environ.get("USERPROFILE") or Path.home())
        return home / "Ipelets"
    return Path.home() / ".ipe" / "ipelets"


def bundled_ipelet() -> Path:
    return Path(__file__).resolve().parents[3] / "ipelet" / "ipebindcraft.lua"


def ipelet_installed() -> Path | None:
    dst = ipelet_install_dir() / "ipebindcraft.lua"
    return dst if dst.is_file() else None


def install_ipelet() -> Path:
    src = bundled_ipelet()
    if not src.is_file():
        raise IbcError("INTERNAL", f"bundled ipelet missing: {src}")
    dst = ipelet_install_dir()
    dst.mkdir(parents=True, exist_ok=True)
    target = dst / "ipebindcraft.lua"
    target.write_bytes(src.read_bytes())
    return target


def _ascii_session_root() -> Path:
    """Session dirs must be ASCII-only: the ipelet uses io.open + cmd dir."""
    base = Path(tempfile.gettempdir()) / "ipe-bindcraft-live"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="s", dir=str(base)))


@dataclass
class LiveBridge:
    """One bound GUI window's session directory + process handle."""

    dir: Path
    epoch: int
    proc: subprocess.Popen | None
    doc_path: Path
    _req: int = 0

    # ---- lifecycle ----------------------------------------------------------

    @classmethod
    def start(cls, ipe_exe: str, doc_path: Path,
              timeout: float = START_TIMEOUT_S) -> "LiveBridge":
        """Launch `ipe <doc>` bound to a fresh session dir via env autostart."""
        if not ipelet_installed():
            raise IbcError(
                "BRIDGE_UNAVAILABLE",
                "ipebindcraft.lua is not installed in ~/.ipe/ipelets",
                details={"install": "ipe-bindcraft install-ipelet"},
            )
        sess_dir = _ascii_session_root()
        env = dict(os.environ)
        env["IPE_BINDCRAFT_LIVE_DIR"] = str(sess_dir)
        try:
            proc = subprocess.Popen(
                [ipe_exe, str(doc_path)],
                env=env,
                cwd=str(doc_path.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise IbcError("BRIDGE_UNAVAILABLE",
                           f"could not launch ipe: {exc}") from exc
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise IbcError("BRIDGE_UNAVAILABLE",
                               f"ipe exited during bind (rc={proc.returncode})")
            if (sess_dir / "session.txt").is_file():
                meta = _parse_kv((sess_dir / "session.txt").read_text(
                    encoding="utf-8", errors="replace"))
                return cls(dir=sess_dir, epoch=int(meta.get("epoch", "0")),
                           proc=proc, doc_path=doc_path)
            time.sleep(0.1)
        proc.terminate()
        raise IbcError(
            "BRIDGE_UNAVAILABLE",
            "GUI started but the ipelet never bound a session "
            "(is ipebindcraft.lua the same version in ~/.ipe/ipelets?)",
        )

    def alive(self) -> bool:
        return self.proc is None or self.proc.poll() is None

    def close(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    # ---- request/response ----------------------------------------------------

    def _submit(self, kind: str, page_xml: bytes | None = None,
                timeout: float = APPLY_TIMEOUT_S) -> tuple[str, str, Path]:
        self._req += 1
        rdir = self.dir / f"req-{self._req}"
        rdir.mkdir()
        (rdir / "meta.txt").write_text(
            f"kind={kind}\nepoch={self.epoch}\n", encoding="utf-8")
        if page_xml is not None:
            (rdir / "candidate.ipepage").write_bytes(page_xml)

        resp = rdir / "resp.txt"
        deadline = time.monotonic() + timeout
        consumed = False
        while time.monotonic() < deadline:
            if resp.is_file():
                # brief settle: resp.txt is written in one io.open call, but
                # give the writer a moment so we never read a partial line.
                time.sleep(0.02)
                lines = resp.read_text(
                    encoding="utf-8", errors="replace").splitlines()
                status = lines[0].strip() if lines else "fail"
                detail = "\n".join(lines[1:]) if len(lines) > 1 else ""
                if status in RESP_STATUSES:
                    return status, detail, rdir
            if not self.alive():
                if consumed:
                    raise IbcError(
                        "COMMIT_STATUS_UNKNOWN",
                        "GUI exited while a request was in flight",
                    )
                raise IbcError("BRIDGE_UNAVAILABLE", "ipe process exited")
            if consumed and time.monotonic() > deadline - timeout / 2:
                # request dir was noticed but no response for >half the
                # timeout: the GUI may still be busy or may have crashed.
                raise IbcError(
                    "COMMIT_STATUS_UNKNOWN",
                    "request consumed by GUI but no response arrived",
                )
            consumed = consumed or any(
                p.name != "meta.txt" and p.name != "candidate.ipepage"
                for p in rdir.iterdir())
            time.sleep(POLL_INTERVAL_S)
        raise IbcError(
            "GUI_BUSY",
            f"no bridge response within {timeout:.0f}s",
            details={"session_dir": str(self.dir)},
        )

    # ---- high-level ops --------------------------------------------------------

    def fetch_page(self) -> bytes:
        """Authoritative current page XML (<ipepage>), no side effects."""
        status, detail, rdir = self._submit("status")
        if status != "ok":
            raise IbcError("INTERNAL", f"bridge status failed: {detail}")
        page = rdir / "page.ipepage"
        if not page.is_file():
            raise IbcError("INTERNAL", "bridge did not write page.ipepage")
        return page.read_bytes()

    def apply_page(self, page_xml: bytes) -> str:
        """Submit a candidate page. Returns 'ok' or raises.

        'conflict' -> the GUI page diverged from baseline: caller must
        refresh + surface REVISION_CONFLICT.
        """
        status, detail, _ = self._submit("apply", page_xml)
        if status == "ok":
            return "ok"
        if status == "conflict":
            return "conflict"
        if status == "stale-epoch":
            raise IbcError("SESSION_EXPIRED",
                           f"bridge session epoch mismatch: {detail}")
        raise IbcError("INTERNAL", f"bridge apply failed: {detail}")

    def undo(self) -> None:
        status, detail, _ = self._submit("undo")
        if status == "fail" and "nothing to undo" in detail:
            raise IbcError("NOT_FOUND", "nothing to undo in GUI")
        if status != "ok":
            raise IbcError("INTERNAL", f"bridge undo failed: {detail}")

    def redo(self) -> None:
        status, detail, _ = self._submit("redo")
        if status == "fail" and "nothing to redo" in detail:
            raise IbcError("NOT_FOUND", "nothing to redo in GUI")
        if status != "ok":
            raise IbcError("INTERNAL", f"bridge redo failed: {detail}")

    def status(self) -> dict:
        st = self.dir / "status.txt"
        if not st.is_file():
            return {"state": "unknown"}
        return _parse_kv(st.read_text(encoding="utf-8", errors="replace"))


def _parse_kv(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def probe(ipe_exe: str | None) -> dict:
    """M3 capability probe: can a live bridge run in this environment?"""
    return {
        "ipe_exe": ipe_exe,
        "ipe_present": bool(ipe_exe) and Path(ipe_exe).is_file(),
        "ipelet_bundled": bundled_ipelet().is_file(),
        "ipelet_installed": str(ipelet_installed() or ""),
        "install_dir": str(ipelet_install_dir()),
        "session_root": str(Path(tempfile.gettempdir()) / "ipe-bindcraft-live"),
    }
