"""M3 acceptance tests: live GUI backend against a real ipe.exe.

Skipped unless: Windows + ipe.exe discovered + ipebindcraft.lua installed.
Each test launches a real Ipe window bound to a private session dir and
closes it afterwards. Run with: pytest tests/windows_live -v
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from ipe_bindcraft import discovery
from ipe_bindcraft.backends import live_backend
from ipe_bindcraft.document import IpeDoc
from ipe_bindcraft.errors import IbcError
from ipe_bindcraft.schemas import ApplyOperationsInput
from ipe_bindcraft.service import Service

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "system_overview.ipe"

tools = discovery.discover()
pytestmark = pytest.mark.skipif(
    os.name != "nt"
    or not tools.ipe
    or not live_backend.ipelet_installed()
    or not FIXTURE.is_file(),
    reason="requires Windows + ipe.exe + installed ipebindcraft.lua ipelet",
)


@pytest.fixture()
def live(tmp_path):
    """Open a live session on a fixture copy; close the GUI afterwards."""
    target = tmp_path / "fig.ipe"
    target.write_bytes(FIXTURE.read_bytes())
    svc = Service()
    info = svc.open_document(str(target), backend="live")
    sess = svc.sessions[info["document_id"]]
    yield svc, info, sess
    svc.close_document(info["document_id"])


def _apply(svc, did, rev, rid, ops):
    return svc.apply_operations(ApplyOperationsInput(
        document_id=did, expected_revision=rev, request_id=rid,
        operations=ops))


NODE = {"op": "node.create", "id": "lb",
        "box": {"x": 300, "y": 200, "width": 100, "height": 40},
        "shape": "rect", "label": {"mode": "plain", "text": "MARKER"}}


def _gui_has(sess, needle: bytes) -> bool:
    return needle in sess.live.fetch_page()


class TestLiveBackend:
    def test_open_binds_and_adopts_gui_page(self, live):
        svc, info, sess = live
        assert info["backend"] == "live"
        assert info["objects"] == 16  # fixture's managed objects visible
        assert sess.live.alive()
        assert (sess.live.dir / "session.txt").is_file()

    def test_apply_is_native_undoable(self, live):
        """A17/A18: apply through model:register; native undo/redo replays."""
        svc, info, sess = live
        did, rev = info["document_id"], info["revision"]
        res = _apply(svc, did, rev, "t1", [NODE])
        assert res["effects"]["created"] == 1
        assert _gui_has(sess, b">MARKER<")

        sess.live.undo()
        assert not _gui_has(sess, b">MARKER<")
        sess.live.redo()
        assert _gui_has(sess, b">MARKER<")
        # replay again — transactions stored as XML never dangle
        sess.live.undo()
        assert not _gui_has(sess, b">MARKER<")
        sess.live.redo()
        assert _gui_has(sess, b">MARKER<")

    def test_revision_conflict_on_stale_expected(self, live):
        svc, info, sess = live
        did = info["document_id"]
        with pytest.raises(IbcError) as ei:
            _apply(svc, did, "sha256:deadbeef", "t2", [NODE])
        assert ei.value.code == "REVISION_CONFLICT"

    def test_gui_divergence_detected(self, live):
        """If the GUI page changed since baseline, apply -> conflict ->
        service refreshes + reports REVISION_CONFLICT."""
        svc, info, sess = live
        did, rev = info["document_id"], info["revision"]
        # simulate a manual GUI edit: corrupt the baseline so the bridge
        # sees current != baseline
        baseline = sess.live.dir / "baseline.ipepage"
        baseline.write_text("<ipepage><page/></ipepage>", encoding="utf-8")
        with pytest.raises(IbcError) as ei:
            _apply(svc, did, rev, "t3", [NODE])
        assert ei.value.code == "REVISION_CONFLICT"
        # session adopted the GUI page and stays usable
        res = _apply(svc, did, sess.revision, "t4", [NODE])
        assert res["effects"]["created"] == 1

    def test_session_expired_when_gui_exits(self, live):
        svc, info, sess = live
        sess.live.close()
        time.sleep(0.3)
        with pytest.raises(IbcError) as ei:
            _apply(svc, info["document_id"], info["revision"], "t5", [NODE])
        assert ei.value.code in ("SESSION_EXPIRED", "BRIDGE_UNAVAILABLE")

    def test_two_live_sessions_isolated(self, tmp_path):
        """A17: two bound windows, ops on one never touch the other."""
        svc = Service()
        t1 = tmp_path / "a.ipe"; t1.write_bytes(FIXTURE.read_bytes())
        t2 = tmp_path / "b.ipe"; t2.write_bytes(FIXTURE.read_bytes())
        i1 = svc.open_document(str(t1), backend="live")
        i2 = svc.open_document(str(t2), backend="live")
        s1, s2 = svc.sessions[i1["document_id"]], svc.sessions[i2["document_id"]]
        assert s1.live.dir != s2.live.dir
        try:
            _apply(svc, i1["document_id"], i1["revision"], "iso-1", [NODE])
            assert _gui_has(s1, b">MARKER<")
            assert not _gui_has(s2, b">MARKER<")
            # revisions/pages stay independent
            assert svc.inspect_document(i2["document_id"])["revision"] \
                == i2["revision"]
        finally:
            svc.close_document(i1["document_id"])
            svc.close_document(i2["document_id"])

    def test_save_writes_authoritative_doc(self, live, tmp_path):
        svc, info, sess = live
        did, rev = info["document_id"], info["revision"]
        _apply(svc, did, rev, "t6", [NODE])
        out = tmp_path / "saved.ipe"
        res = svc.save_document(did, sess.revision, "t6s", path=str(out))
        assert Path(res["saved"]).is_file()
        doc = IpeDoc.load(out)
        assert b"MARKER" in doc.serialize()
