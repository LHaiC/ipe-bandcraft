"""M1/M2 integration tests — require real Ipe + TeX (marked `integration`).

Covers the spec's minimum chain:
create -> batch nodes+edges -> inspect -> move node -> reroute -> lint ->
image preview -> export -> (simulated human edit in the XML) -> reopen ->
local modification.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from ipe_bindcraft import discovery
from ipe_bindcraft.document import IpeDoc
from ipe_bindcraft.errors import IbcError
from ipe_bindcraft.schemas import ApplyOperationsInput, Operation
from ipe_bindcraft.service import Service
from ipe_bindcraft.snapshot import build_snapshot

_OPS = TypeAdapter(list[Operation])
pytestmark = pytest.mark.integration

TOOLS = discovery.discover()
requires_ipe = pytest.mark.skipif(
    not (TOOLS.ipetoipe and TOOLS.iperender and TOOLS.ipescript),
    reason="Ipe tools not found")
requires_tex = pytest.mark.skipif(
    not (TOOLS.tex.get("pdflatex") and TOOLS.tex["pdflatex"].found),
    reason="pdflatex not found; LaTeX measurement unavailable")


def apply(svc, did, rev, rid, ops, dry_run=False):
    return svc.apply_operations(ApplyOperationsInput(
        document_id=did, expected_revision=rev, request_id=rid,
        operations=_OPS.validate_python(ops), dry_run=dry_run))


NODE = lambda oid, x, y: {"op": "node.create", "id": oid, "shape": "rect",
                          "box": {"x": x, "y": y, "width": 80, "height": 30},
                          "label": {"text": oid, "mode": "plain"}}


@requires_ipe
@requires_tex
def test_full_chain(tmp_path):
    svc = Service(TOOLS)
    doc = svc.create_document(str(tmp_path / "fig.ipe"), 400, 300, "paper-default")
    did = doc["document_id"]

    # batch create
    r = apply(svc, did, doc["revision"], "c1", [
        NODE("a", 40, 40), NODE("b", 240, 40),
        {"op": "edge.create", "id": "ab", "source": {"node": "a"},
         "target": {"node": "b"}, "routing": {"mode": "straight"}},
    ])
    assert r["effects"]["created"] == 3 and r["effects"]["rerouted"] == 1

    # inspect
    insp = svc.inspect_document(did, include_geometry=True)
    assert {o["id"] for o in insp["objects"]} == {"a", "b", "ab"}
    edge = next(o for o in insp["objects"] if o["id"] == "ab")
    assert edge["needs_route"] is False

    # move node -> edges reroute automatically
    r = apply(svc, did, insp["revision"], "c2", [
        {"op": "objects.translate", "ids": ["b"], "dx": 0, "dy": 60}])
    insp2 = svc.inspect_document(did, include_geometry=True)
    edge2 = next(o for o in insp2["objects"] if o["id"] == "ab")
    assert edge2["bbox"][3] > 40  # now diagonal, not flat

    # lint
    lint = svc.lint_figure(did)
    assert lint["summary"]["fail"] == 0

    # preview png at same revision
    from ipe_bindcraft.export import render_preview_png
    png = render_preview_png(TOOLS, svc._session(did).doc.serialize(), dpi=100)
    assert png[:4] == b"\x89PNG"

    # export
    from ipe_bindcraft.export import export_figure, verify_manifest
    m = export_figure(TOOLS, svc._session(did).doc.serialize(),
                      insp2["revision"], str(tmp_path / "out"), "fig",
                      ["pdf", "svg", "png"])
    assert verify_manifest(m["manifest_path"])["ok"]

    # --- simulated human edit: open the file, change a label, add a free object
    raw = (tmp_path / "fig.ipe").read_text(encoding="utf-8")
    raw = raw.replace(">a<", ">human-renamed<", 1)
    raw = raw.replace("</page>",
                      '<path stroke="black">5 5 m\n30 5 l</path>\n</page>')
    (tmp_path / "fig.ipe").write_text(raw, encoding="utf-8")

    # reopen: human edits must be preserved and visible
    svc2 = Service(TOOLS)
    doc2 = svc2.open_document(str(tmp_path / "fig.ipe"))
    insp3 = svc2.inspect_document(doc2["document_id"])
    assert insp3["unmanaged_objects"] >= 1  # the free path survived
    # local modification after human edit
    r = apply(svc2, doc2["document_id"], doc2["revision"], "c3", [
        {"op": "text.create", "id": "note", "x": 40, "y": 250,
         "text": {"text": "post-edit", "mode": "plain"}}])
    txt = (tmp_path / "fig.ipe").read_text(encoding="utf-8")
    assert "human-renamed" in txt  # human label preserved
    assert "5 5 m" in txt          # human path preserved


@requires_ipe
def test_batch_atomicity(tmp_path):
    svc = Service(TOOLS)
    doc = svc.create_document(str(tmp_path / "f.ipe"), 400, 300, "paper-default")
    rev = doc["revision"]
    with pytest.raises(IbcError) as e:
        apply(svc, doc["document_id"], rev, "bad-batch", [
            NODE("ok1", 10, 10),
            {"op": "edge.create", "id": "bad", "source": {"node": "ok1"},
             "target": {"node": "ghost"}, "routing": {"mode": "straight"}},
        ])
    assert e.value.code == "DANGLING_EDGE"
    # nothing committed
    insp = svc.inspect_document(doc["document_id"])
    assert insp["objects"] == []


@requires_ipe
def test_file_lock_blocks_second_writer(tmp_path):
    svc = Service(TOOLS)
    doc = svc.create_document(str(tmp_path / "f.ipe"), 400, 300, "paper-default")
    from ipe_bindcraft.backends.file_backend import WriterLock
    lock = WriterLock(tmp_path / "f.ipe")
    lock.acquire(timeout=1)
    try:
        with pytest.raises(IbcError) as e:
            lock2 = WriterLock(tmp_path / "f.ipe")
            lock2.acquire(timeout=0.5)
    finally:
        lock.release()
    assert e.value.code == "FILE_BUSY"


@requires_ipe
@requires_tex
def test_template_palette_icon_auto(tmp_path):
    """create_document(template=..., palette=...) instantiates real content;
    icon.create produces a managed group; routing 'auto' picks sensible modes."""
    svc = Service(TOOLS)
    doc = svc.create_document(str(tmp_path / "t.ipe"), 460, 300, "paper-default",
                              palette="paper-vivid", template="parallel_workers")
    insp = svc.inspect_document(doc["document_id"], include_geometry=True)
    ids = {o["id"] for o in insp["objects"]}
    # template content actually landed
    assert {"disp", "w1", "w4", "join", "sink"} <= ids
    assert doc["template"] == "parallel_workers"

    # 'auto' edge between row-aligned nodes -> straight (flat bbox);
    # diagonal -> orthogonal (bent bbox)
    r = apply(svc, doc["document_id"], insp["revision"], "auto1", [
        NODE("n1", 30, 260), NODE("n2", 160, 262), NODE("n3", 300, 170),
        {"op": "edge.create", "id": "ea", "source": {"node": "n1"},
         "target": {"node": "n2"}, "routing": {"mode": "auto"}},
        {"op": "edge.create", "id": "eb", "source": {"node": "n1"},
         "target": {"node": "n3"}, "routing": {"mode": "auto"}},
        {"op": "icon.create", "id": "ic", "name": "database",
         "box": {"x": 400, "y": 250, "width": 30, "height": 30}},
    ])
    assert r["effects"]["created"] == 6
    insp2 = svc.inspect_document(doc["document_id"], include_geometry=True)
    by_id = {o["id"]: o for o in insp2["objects"]}
    assert by_id["ic"]["kind"] == "icon"
    # straight edge: bbox height ~ line thickness; orthogonal: wide AND tall
    assert by_id["ea"]["bbox"][3] < 5
    assert by_id["eb"]["bbox"][2] > 50 and by_id["eb"]["bbox"][3] > 30
    # icon renders without errors
    from ipe_bindcraft.export import render_preview_png
    png = render_preview_png(TOOLS, svc._session(doc["document_id"]).doc.serialize(), dpi=100)
    assert png[:4] == b"\x89PNG"


@requires_ipe
@requires_tex
def test_tex_failure_fails_fast(tmp_path):
    svc = Service(TOOLS)
    doc = svc.create_document(str(tmp_path / "f.ipe"), 400, 300, "paper-default")
    with pytest.raises(IbcError) as e:
        apply(svc, doc["document_id"], doc["revision"], "tex-bad", [
            {"op": "text.create", "id": "t", "x": 10, "y": 10,
             "text": {"text": "\\definitelynotacommand{", "mode": "latex"}}])
    assert e.value.code == "LATEX_FAILED"
    # document unchanged
    assert svc.inspect_document(doc["document_id"])["objects"] == []
