"""Shared service layer for the CLI and the MCP server.

Owns the document registry, revision/dedup checks, the compile->reroute->
measure->commit pipeline, and uniform error semantics. Both the stdio MCP
server and the CLI are thin adapters over this class.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import discovery, routing, styles
from .backends.file_backend import Journal, WriterLock, atomic_write, file_revision
from .backends.live_backend import LiveBridge, install_ipelet, probe as live_probe
from .compiler import Compiler
from .document import IpeDoc
from .errors import IbcError, OpError
from .schemas import (
    ApplyOperationsInput,
    Operation,
    check_finite_recursive,
)
from .snapshot import build_snapshot
from .tex import TexMeasurer

FAULT_ENV = "IBC_FAULT"  # fault-injection hook for tests: comma list of stages


def _log(msg: str):
    sys.stderr.write(f"[ipe-bindcraft] {msg}\n")
    sys.stderr.flush()


def _fault(name: str) -> bool:
    return name in (os.environ.get(FAULT_ENV) or "").split(",")


@dataclass
class Session:
    document_id: str
    path: Path
    backend: str
    doc: IpeDoc
    revision: str
    lock: threading.Lock = field(default_factory=threading.Lock)
    journal: Journal | None = None
    opened_at: float = field(default_factory=time.time)
    live: LiveBridge | None = None


class Service:
    def __init__(self, tools: discovery.Discovery | None = None):
        self._tools = tools
        self.sessions: dict[str, Session] = {}
        self._counter = 0
        self._measurer: TexMeasurer | None = None
        self._global_lock = threading.Lock()

    # ---- discovery --------------------------------------------------------

    def tools(self) -> discovery.Discovery:
        if self._tools is None:
            self._tools = discovery.discover()
        return self._tools

    def doctor(self) -> dict:
        t = self.tools()
        rep = t.report()
        rep["live_bridge"] = live_probe(str(t.ipe) if t.ipe else None)
        return rep

    def _measurer_for(self) -> TexMeasurer:
        if self._measurer is None:
            t = self.tools()
            if not t.ipescript:
                raise IbcError(
                    "BACKEND_CAPABILITY_UNAVAILABLE",
                    "ipescript not found; LaTeX measurement unavailable",
                    details=t.report(),
                )
            self._measurer = TexMeasurer(str(t.ipescript))
        return self._measurer

    # ---- documents ----------------------------------------------------------

    def create_document(self, path: str, width_bp: float, height_bp: float,
                        style_id: str, tex_profile: str = "paper-serif") -> dict:
        p = Path(path)
        if p.exists() and p.stat().st_size > 0:
            raise IbcError("VALIDATION", f"refusing to overwrite non-empty file {p}")
        if style_id not in styles.STYLE_DEFAULTS:
            raise IbcError("VALIDATION", f"unknown style_id {style_id!r}",
                           details={"known": list(styles.STYLE_DEFAULTS)})
        prof = styles.TEX_PROFILES.get(tex_profile)
        if prof is None:
            raise IbcError("VALIDATION", f"unknown tex_profile {tex_profile!r}")
        p.parent.mkdir(parents=True, exist_ok=True)
        doc = IpeDoc.new(width_bp, height_bp,
                         styles.stylesheet_xml(style_id, width_bp, height_bp),
                         preamble=prof["preamble"])
        doc.set_doc_meta(style_id=style_id, tex_profile=tex_profile,
                         palette=styles.STYLE_DEFAULTS[style_id]["palette"],
                         created_by="ipe-bindcraft")
        atomic_write(p, doc.serialize())
        return self._open_session(p, backend="file")

    def open_document(self, path: str, backend: str = "file",
                      bridge_session_id: str | None = None) -> dict:
        p = Path(path)
        if not p.is_file():
            raise IbcError("NOT_FOUND", f"no such file: {p}")
        if backend == "live":
            return self._open_live(p)
        if backend != "file":
            raise IbcError("VALIDATION", f"unknown backend {backend!r}")
        return self._open_session(p, backend="file")

    def _open_live(self, p: Path) -> dict:
        t = self.tools()
        if not t.ipe:
            raise IbcError("BRIDGE_UNAVAILABLE", "ipe.exe not found",
                           details=t.report())
        bridge = LiveBridge.start(str(t.ipe), p)
        try:
            doc = IpeDoc.load(p)
            doc.replace_page(bridge.fetch_page())
        except Exception:
            bridge.close()
            raise
        with self._global_lock:
            self._counter += 1
            did = f"doc-{self._counter:04d}"
            sess = Session(
                document_id=did, path=p.resolve(), backend="live", doc=doc,
                revision="sha256:" + doc.content_hash()[:32],
                journal=Journal(p), live=bridge,
            )
            self.sessions[did] = sess
        snap = build_snapshot(doc)
        return {
            "document_id": did,
            "path": str(p),
            "backend": "live",
            "revision": sess.revision,
            "session_dir": str(bridge.dir),
            "page_size_bp": list(doc.page_size),
            "objects": len(snap.objects),
            "unmanaged_objects": len(snap.unmanaged),
            "duplicates": sorted(snap.duplicates),
        }

    def _open_session(self, p: Path, backend: str) -> dict:
        doc = IpeDoc.load(p)
        with self._global_lock:
            self._counter += 1
            did = f"doc-{self._counter:04d}"
            sess = Session(
                document_id=did, path=p.resolve(), backend=backend, doc=doc,
                revision=file_revision(p), journal=Journal(p),
            )
            self.sessions[did] = sess
        snap = build_snapshot(doc)
        return {
            "document_id": did,
            "path": str(p),
            "backend": backend,
            "revision": sess.revision,
            "page_size_bp": list(doc.page_size),
            "objects": len(snap.objects),
            "unmanaged_objects": len(snap.unmanaged),
            "duplicates": sorted(snap.duplicates),
        }

    def close_document(self, document_id: str) -> dict:
        sess = self.sessions.pop(document_id, None)
        if sess is None:
            raise IbcError("NOT_FOUND", f"no session {document_id!r}")
        if sess.live is not None:
            sess.live.close()
        return {"closed": document_id}

    def _session(self, document_id: str) -> Session:
        sess = self.sessions.get(document_id)
        if sess is None:
            raise IbcError("NOT_FOUND", f"no session {document_id!r}")
        return sess

    # ---- inspection ---------------------------------------------------------

    def inspect_document(self, document_id: str, ids: list[str] | None = None,
                         include_geometry: bool = False) -> dict:
        sess = self._session(document_id)
        self._refresh_if_changed(sess)
        snap = build_snapshot(sess.doc)
        out = []
        wanted = set(ids) if ids else None
        for oid, obj in snap.objects.items():
            if wanted is not None and oid not in wanted:
                continue
            rec = {"id": oid, "kind": obj.kind, "layer": obj.layer}
            if include_geometry:
                bb = obj.bbox(sess.doc)
                rec["bbox"] = [bb.x, bb.y, bb.width, bb.height] if bb else None
            if obj.kind == "edge":
                rec["source"] = obj.source
                rec["target"] = obj.target
                rec["routing"] = obj.routing
                rec["needs_route"] = obj.needs_route
            if obj.kind == "node":
                rec["shape"] = obj.shape
            out.append(rec)
        missing = (wanted or set()) - {r["id"] for r in out}
        return {
            "document_id": document_id,
            "revision": sess.revision,
            "objects": out,
            "missing_ids": sorted(missing),
            "unmanaged_objects": len(snap.unmanaged),
            "duplicates": sorted(snap.duplicates),
            "page_size_bp": list(sess.doc.page_size),
        }

    def _refresh_if_changed(self, sess: Session):
        """Adopt a new revision if the authoritative source changed.

        File mode: the .ipe bytes on disk. Live mode: the bound GUI page —
        manual edits in the GUI are fetched and adopted, they are the
        authoritative content (SPEC: the bound document is the source).
        """
        if sess.live is not None:
            if not sess.live.alive():
                raise IbcError("SESSION_EXPIRED",
                               "bound Ipe GUI exited; live session is over")
            page = sess.live.fetch_page()
            probe = sess.doc.clone()
            probe.replace_page(page)
            rev = "sha256:" + probe.content_hash()[:32]
            if rev != sess.revision:
                sess.doc = probe
                sess.revision = rev
            return
        disk = file_revision(sess.path)
        if disk != sess.revision and disk != "missing":
            sess.doc = IpeDoc.load(sess.path)
            sess.revision = disk

    # ---- write pipeline -------------------------------------------------------

    def apply_operations(self, inp: ApplyOperationsInput) -> dict:
        sess = self._session(inp.document_id)
        with sess.lock:
            return self._apply_locked(sess, inp.operations, inp.expected_revision,
                                      inp.request_id, inp.dry_run)

    def _apply_locked(self, sess: Session, ops: list[Operation],
                      expected_revision: str, request_id: str,
                      dry_run: bool) -> dict:
        # dedup: same request_id + same payload -> cached result; different
        # payload -> REQUEST_ID_REUSED.
        prior = sess.journal.get(request_id) if sess.journal else None
        fp = hashlib.sha256(json.dumps(
            [o.model_dump(mode="json") for o in ops],
            sort_keys=True).encode()).hexdigest()[:16]
        if prior:
            if prior.get("fingerprint") == fp and prior.get("status") == "committed":
                return {**prior["result"], "deduplicated": True}
            if prior.get("fingerprint") != fp:
                raise IbcError(
                    "REQUEST_ID_REUSED",
                    f"request_id {request_id!r} was already used with a different payload",
                )
            # same payload, never committed: previous attempt crashed
            raise IbcError(
                "COMMIT_STATUS_UNKNOWN",
                f"request {request_id!r} started but never committed "
                f"(status={prior.get('status')}); inspect the document before retrying",
            )

        # source race check before doing any work
        if sess.live is not None:
            self._refresh_if_changed(sess)
        else:
            disk = file_revision(sess.path)
            if disk != sess.revision:
                self._refresh_if_changed(sess)
        if expected_revision != sess.revision:
            raise IbcError(
                "REVISION_CONFLICT",
                f"expected {expected_revision}, current is {sess.revision}",
                details={"expected": expected_revision, "current": sess.revision},
            )

        candidate = sess.doc.clone()
        snap = build_snapshot(candidate)
        comp = Compiler(candidate, snap)
        try:
            res = comp.apply(ops)
        except OpError:
            raise
        except IbcError:
            raise
        except Exception as exc:  # never leak a raw traceback as a result
            raise IbcError("INTERNAL", f"compile failed: {exc}") from exc

        rerouted, route_warns = routing.reroute_stale_edges(candidate, snap)
        res.effects["rerouted"] = rerouted
        for w in route_warns:
            res.warnings.append({"kind": "routing", "message": w})

        return self._commit_candidate(
            sess, candidate, res.text_dirty, fp, request_id, dry_run,
            extra={"effects": res.effects, "changed_ids": res.changed_ids,
                   "warnings": res.warnings},
            op_count=len(ops),
        )

    def _commit_candidate(self, sess: Session, candidate: IpeDoc,
                          text_dirty: bool, fp: str, request_id: str,
                          dry_run: bool, extra: dict,
                          op_count: int = 0) -> dict:
        """Shared tail: measure -> revision check -> atomic write -> journal."""
        body = candidate.serialize()
        changed = candidate.content_hash() != sess.doc.content_hash()
        if _fault("before_measure"):
            raise IbcError("INTERNAL", "injected fault: before_measure")
        if text_dirty:
            measurer = self._measurer_for()
            body = measurer.measure_doc(body)
            candidate = IpeDoc.parse(body)

        result = {
            "document_id": sess.document_id,
            "request_id": request_id,
            "dry_run": dry_run,
            "deduplicated": False,
            **extra,
        }
        if dry_run:
            result["revision"] = sess.revision
            return result
        if not changed and not text_dirty:
            result["revision"] = sess.revision
            result["noop"] = True
            return result

        if sess.journal:
            sess.journal.append({"request_id": request_id, "fingerprint": fp,
                                 "status": "started", "revision": sess.revision})
        if _fault("before_commit"):
            raise IbcError("INTERNAL", "injected fault: before_commit")

        if sess.live is not None:
            return self._commit_live(sess, candidate, fp, request_id, result,
                                     op_count)

        lock = WriterLock(sess.path)
        lock.acquire()
        try:
            # final race check under the lock
            disk = file_revision(sess.path)
            if disk != sess.revision:
                raise IbcError(
                    "REVISION_CONFLICT",
                    "file changed on disk between check and commit",
                    details={"expected": sess.revision, "current": disk},
                )
            atomic_write(sess.path, body)
        finally:
            lock.release()

        if _fault("after_commit"):
            raise IbcError("COMMIT_STATUS_UNKNOWN",
                           "injected fault after commit; result uncertain")

        sess.doc = IpeDoc.parse(body)
        sess.revision = file_revision(sess.path)
        result["revision"] = sess.revision
        if sess.journal:
            sess.journal.append({"request_id": request_id, "fingerprint": fp,
                                 "status": "committed", "revision": sess.revision,
                                 "result": result})
        _log(f"committed {op_count} op(s) on {sess.path.name} -> {sess.revision}")
        return result

    def _commit_live(self, sess: Session, candidate: IpeDoc, fp: str,
                     request_id: str, result: dict, op_count: int) -> dict:
        """Push a candidate page through the bridge as one undoable action."""
        assert sess.live is not None
        status = sess.live.apply_page(candidate.page_xml())
        if status == "conflict":
            # GUI page diverged from our baseline: adopt it, then tell the
            # caller to retry against the fresh revision.
            self._refresh_if_changed(sess)
            raise IbcError(
                "REVISION_CONFLICT",
                "document changed in the bound GUI; re-read revision and retry",
                details={"current": sess.revision},
            )
        # adopt the GUI-normalized page as the authoritative doc state
        page = sess.live.fetch_page()
        sess.doc = candidate.clone()
        sess.doc.replace_page(page)
        sess.revision = "sha256:" + sess.doc.content_hash()[:32]
        result["revision"] = sess.revision
        if sess.journal:
            sess.journal.append({"request_id": request_id, "fingerprint": fp,
                                 "status": "committed", "revision": sess.revision,
                                 "result": result})
        _log(f"live commit {op_count} op(s) on {sess.path.name} -> {sess.revision}")
        return result

    # ---- higher-level tools ----------------------------------------------------

    def route_edges(self, document_id: str, edge_ids: list[str],
                    expected_revision: str, request_id: str,
                    dry_run: bool = False) -> dict:
        from .metadata import encode_meta

        sess = self._session(document_id)
        with sess.lock:
            self._refresh_if_changed(sess)
            if expected_revision != sess.revision:
                raise IbcError("REVISION_CONFLICT",
                               f"expected {expected_revision}, current {sess.revision}")
            candidate = sess.doc.clone()
            snap = build_snapshot(candidate)
            for eid in edge_ids:
                edge = snap.edges.get(eid)
                if edge is None:
                    raise IbcError("NOT_FOUND", f"edge {eid!r} not found",
                                   details={"id": eid})
                meta = dict(edge.meta)
                meta["needs_route"] = True
                edge.el.set("custom", encode_meta(meta))
            n, warns = routing.reroute_stale_edges(candidate, snap)
            fp = hashlib.sha256(
                ("route:" + ",".join(sorted(edge_ids))).encode()).hexdigest()[:16]
            prior = sess.journal.get(request_id) if sess.journal else None
            if prior and prior.get("fingerprint") == fp and prior.get("status") == "committed":
                return {**prior["result"], "deduplicated": True}
            return self._commit_candidate(
                sess, candidate, False, fp, request_id, dry_run,
                extra={"effects": {"rerouted": n},
                       "warnings": [{"kind": "routing", "message": w} for w in warns]},
            )

    def layout_objects(self, document_id: str, ids: list[str], mode: str,
                       options: dict | None, expected_revision: str,
                       request_id: str, dry_run: bool = True) -> dict:
        from . import layout as lay
        from .schemas import ObjectsTranslate

        sess = self._session(document_id)
        self._refresh_if_changed(sess)
        snap = build_snapshot(sess.doc)
        deltas = lay.compute_layout(snap, ids, mode, options)
        if not deltas:
            return {"document_id": document_id, "revision": sess.revision,
                    "noop": True, "moved": {}, "dry_run": dry_run}
        if dry_run:
            return {"document_id": document_id, "revision": sess.revision,
                    "dry_run": True, "moved": deltas}
        ops = [ObjectsTranslate(ids=[oid], dx=dx, dy=dy)
               for oid, (dx, dy) in deltas.items()]
        with sess.lock:
            res = self._apply_locked(sess, ops, expected_revision, request_id, False)
        res["moved"] = deltas
        return res

    def lint_figure(self, document_id: str, revision: str | None = None,
                    target_width_bp: float | None = None) -> dict:
        from .quality import lint

        sess = self._session(document_id)
        self._refresh_if_changed(sess)
        if revision is not None and revision != sess.revision:
            raise IbcError("REVISION_CONFLICT",
                           f"lint requested at {revision}, current {sess.revision}")
        snap = build_snapshot(sess.doc)
        out = lint(snap, target_width_bp)
        out["document_id"] = document_id
        out["revision"] = sess.revision
        return out

    def polish_figure(self, document_id: str, fixes: list[str],
                      expected_revision: str, request_id: str,
                      dry_run: bool = True) -> dict:
        from .quality import polish_plan
        from .schemas import NodeUpdate

        sess = self._session(document_id)
        self._refresh_if_changed(sess)
        snap = build_snapshot(sess.doc)
        plan = polish_plan(snap, fixes)
        if dry_run:
            return {"document_id": document_id, "revision": sess.revision,
                    "dry_run": True, "plan": plan}
        # apply only the fixes we can express as typed ops
        ops: list[Operation] = []
        for item in plan:
            if item["fix"] == "grow_nodes_to_label":
                x, y, w, h = item["box"]
                ops.append(NodeUpdate(
                    id=item["id"],
                    changes={"box": {"x": x, "y": y, "width": w, "height": h}},
                ))
        if not ops:
            return {"document_id": document_id, "revision": sess.revision,
                    "noop": True, "plan": plan}
        with sess.lock:
            res = self._apply_locked(sess, ops, expected_revision, request_id, False)
        res["plan"] = plan
        return res

    def get_request_status(self, document_id: str, request_id: str) -> dict:
        sess = self._session(document_id)
        rec = sess.journal.get(request_id) if sess.journal else None
        if rec is None:
            return {"request_id": request_id, "status": "unknown"}
        return {"request_id": request_id, "status": rec.get("status"),
                "revision": rec.get("revision")}

    def save_document(self, document_id: str, expected_revision: str,
                      request_id: str, path: str | None = None) -> dict:
        sess = self._session(document_id)
        with sess.lock:
            if expected_revision != sess.revision:
                raise IbcError("REVISION_CONFLICT",
                               f"expected {expected_revision}, current {sess.revision}")
            target = Path(path) if path else sess.path
            if sess.live is not None:
                # authoritative bytes = file skeleton + bound GUI page
                atomic_write(target, sess.doc.serialize())
                return {"saved": str(target), "revision": sess.revision}
            if target.resolve() != sess.path.resolve():
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(target, sess.doc.serialize())
            else:
                # in-memory doc equals committed bytes; nothing to do unless dirty
                if file_revision(sess.path) == sess.revision:
                    return {"saved": str(sess.path), "revision": sess.revision,
                            "noop": True}
                atomic_write(sess.path, sess.doc.serialize())
            return {"saved": str(target), "revision": sess.revision}

    def list_presets(self, kind: str) -> dict:
        return {"kind": kind, "presets": styles.list_presets(kind)}
