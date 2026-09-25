"""File backend: the .ipe file on disk is the authoritative source.

Semantics (SPEC 6):
- revision = sha256 of the committed document bytes.
- Writes go through a candidate document: parse -> compile -> measure -> save
  to a temp file in the same directory -> os.replace (atomic on Windows).
- A cross-process writer lock uses an exclusive sidecar lockfile with a
  staleness timeout; in-process a per-document threading.Lock serializes work.
- The file is re-stat'ed and re-hashed before commit: if bytes changed since
  the revision the caller pinned, we fail with REVISION_CONFLICT — never a
  blind overwrite.
- request_id results are journaled to <file>.ibc-journal so a crash can be
  distinguished from a clean failure (COMMIT_STATUS_UNKNOWN).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path

from ..errors import IbcError

LOCK_STALE_S = 120.0


def file_revision(path: Path) -> str:
    """Revision id for the file's current bytes (missing file -> 'missing')."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return "missing"
    return "sha256:" + hashlib.sha256(data).hexdigest()[:32]


class WriterLock:
    """Cross-process best-effort lock via an exclusive sidecar file."""

    def __init__(self, target: Path):
        self.lock_path = target.with_name(target.name + ".ibc-lock")
        self._held = False

    def acquire(self, timeout: float = 10.0):
        deadline = time.monotonic() + timeout
        while True:
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, json.dumps({"pid": os.getpid(), "ts": time.time()}).encode())
                os.close(fd)
                self._held = True
                return
            except FileExistsError:
                # stale lock?
                try:
                    age = time.time() - self.lock_path.stat().st_mtime
                    if age > LOCK_STALE_S:
                        self.lock_path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise IbcError(
                        "FILE_BUSY",
                        f"document is locked by another writer: {self.lock_path.name}",
                        details={"lock": str(self.lock_path)},
                    )
                time.sleep(0.05)

    def release(self):
        if self._held:
            self.lock_path.unlink(missing_ok=True)
            self._held = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


class Journal:
    """Append-only request journal next to the document for dedup + crash audit."""

    def __init__(self, target: Path):
        self.path = target.with_name(target.name + ".ibc-journal")
        self._cache: dict[str, dict] | None = None
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict]:
        if self._cache is not None:
            return self._cache
        out: dict[str, dict] = {}
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # torn write at crash: ignore the tail
                if isinstance(rec, dict) and rec.get("request_id"):
                    out[rec["request_id"]] = rec
        except FileNotFoundError:
            pass
        self._cache = out
        return out

    def get(self, request_id: str) -> dict | None:
        return self._load().get(request_id)

    def records(self) -> list[dict]:
        """All journaled records (one per unique request_id, file order)."""
        return list(self._load().values())

    def append(self, rec: dict):
        rec = {"ts": time.time(), **rec}
        line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            self._load()[rec["request_id"]] = rec


def atomic_write(target: Path, data: bytes):
    """Write via temp file in the same directory + os.replace."""
    fd, tmp = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp",
                               dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
