"""Regression tests for the ergonomics/UX improvements (see
docs/improvement-backlog.md, IMP-01..17).

Path-only ops keep text_dirty False so no LaTeX is needed at service level;
compiler-level tests can exercise node ops freely (measurement is a later
service stage).
"""
import json

import pytest
from pydantic import TypeAdapter

from ipe_bindcraft import styles
from ipe_bindcraft.cli import _load_ops
from ipe_bindcraft.compiler import Compiler
from ipe_bindcraft.document import IpeDoc, get_custom
from ipe_bindcraft.errors import UsageError
from ipe_bindcraft.metadata import decode_meta, encode_meta
from ipe_bindcraft.quality import lint, polish_plan, _split_balanced, _unescape_plain
from ipe_bindcraft.schemas import ApplyOperationsInput, Operation
from ipe_bindcraft.service import Service
from ipe_bindcraft.snapshot import build_snapshot

_OPS = TypeAdapter(list[Operation])


def mkdoc(w=400, h=300):
    return IpeDoc.new(w, h, styles.stylesheet_xml("paper-default", w, h))


def compile_ops(doc, ops):
    snap = build_snapshot(doc)
    c = Compiler(doc, snap)
    res = c.apply(_OPS.validate_python(ops))
    return res, snap


def mkdoc_file(tmp_path, w=400, h=300):
    p = tmp_path / "t.ipe"
    d = IpeDoc.new(w, h, styles.stylesheet_xml("paper-default", w, h))
    p.write_bytes(d.serialize())
    return p


def apply(svc, did, rev, rid, ops, dry_run=False):
    inp = ApplyOperationsInput(document_id=did, expected_revision=rev,
                               request_id=rid,
                               operations=_OPS.validate_python(ops),
                               dry_run=dry_run)
    return svc.apply_operations(inp)


NODE = lambda oid, x, y: {
    "op": "node.create", "id": oid, "shape": "rect",
    "box": {"x": x, "y": y, "width": 80, "height": 30},
    "label": {"text": oid, "mode": "plain"}}

PATH = lambda oid, x0, y0, x1, y1: {
    "op": "path.create", "id": oid,
    "segments": [{"cmd": "M", "x": x0, "y": y0}, {"cmd": "L", "x": x1, "y": y1}]}


# --- IMP-01: _load_ops --------------------------------------------------------

def test_load_ops_bare_filename(tmp_path):
    f = tmp_path / "ops.json"
    f.write_text(json.dumps([PATH("p1", 0, 0, 10, 10)]), encoding="utf-8")
    ops = _load_ops(str(f))
    assert len(ops) == 1 and ops[0].op == "path.create"


def test_load_ops_at_file(tmp_path):
    f = tmp_path / "ops.json"
    f.write_text(json.dumps([PATH("p1", 0, 0, 10, 10)]), encoding="utf-8")
    assert _load_ops("@" + str(f))[0].op == "path.create"


def test_load_ops_dict_wrappers():
    raw = json.dumps([PATH("p1", 0, 0, 10, 10)])
    for wrapper in ('{"ops": ' + raw + '}', '{"operations": ' + raw + '}'):
        assert _load_ops(wrapper)[0].op == "path.create"


def test_load_ops_missing_file_hints_at():
    with pytest.raises(UsageError) as e:
        _load_ops("nosuch_ops_file.json")
    assert "@nosuch_ops_file.json" in e.value.message
    assert e.value.code == "USAGE"


def test_load_ops_bad_json(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("not json at all", encoding="utf-8")
    with pytest.raises(UsageError) as e:
        _load_ops("@" + str(f))
    assert "not valid JSON" in e.value.message


def test_load_ops_schema_failure_is_usage():
    with pytest.raises(UsageError) as e:
        _load_ops('[{"op": "bogus.op"}]')
    assert "schema" in e.value.message


def test_load_ops_dict_without_ops():
    with pytest.raises(UsageError) as e:
        _load_ops('{"foo": 1}')
    assert '"ops" array' in e.value.message


# --- IMP-06: objects.move_to ---------------------------------------------------

def test_move_to_top_left():
    d = mkdoc()
    compile_ops(d, [PATH("p1", 10, 10, 50, 50)])
    res, snap = compile_ops(d, [
        {"op": "objects.move_to", "ids": ["p1"], "x": 100, "y": 60}])
    bb = snap.objects["p1"].bbox(d)
    assert (bb.x, bb.y) == (100, 60)
    assert (bb.width, bb.height) == (40, 40)


def test_move_to_center_anchor_and_reroute():
    d = mkdoc()
    res, snap = compile_ops(d, [
        NODE("a", 0, 0), NODE("b", 150, 0),
        {"op": "edge.create", "id": "e", "source": {"node": "a"},
         "target": {"node": "b"}, "routing": {"mode": "straight"}},
    ])
    compile_ops(d, [
        {"op": "objects.move_to", "ids": ["a"], "x": 0, "y": 100,
         "anchor": "center"}])
    bb = snap.objects["a"].bbox(d)
    assert abs(bb.cx - 0) < 0.01 and abs(bb.cy - 100) < 0.01
    assert snap.edges["e"].needs_route


# --- IMP-13: changed_ids dedup -------------------------------------------------

def test_changed_ids_deduplicated():
    d = mkdoc()
    res, _ = compile_ops(d, [
        PATH("p1", 10, 10, 50, 50),
        {"op": "objects.translate", "ids": ["p1"], "dx": 5, "dy": 0},
        {"op": "objects.translate", "ids": ["p1"], "dx": 5, "dy": 0},
    ])
    assert res.changed_ids.count("p1") == 1


# --- IMP-07: readable names + greppable custom tag -----------------------------

def test_name_attr_and_id_tag():
    d = mkdoc()
    compile_ops(d, [PATH("mypath", 10, 10, 50, 50)])
    from lxml import etree
    xml = d.serialize().decode()
    assert 'name="mypath"' in xml
    assert 'custom="ibc1:mypath:' in xml


def test_meta_codec_new_format_and_backcompat():
    enc = encode_meta({"id": "n1", "kind": "node"})
    assert enc.startswith("ibc1:n1:")
    assert decode_meta(enc)["id"] == "n1"
    # part ids contain ':' — tag must still split correctly
    enc2 = encode_meta({"id": "n1:label", "owner": "n1", "part": "label"})
    assert decode_meta(enc2)["id"] == "n1:label"
    # legacy format (no tag) still decodes
    import base64, json as _json
    raw = _json.dumps({"schema": 1, "id": "old", "kind": "node"}).encode()
    legacy = "ibc1:" + base64.urlsafe_b64encode(raw).decode().rstrip("=")
    assert decode_meta(legacy)["id"] == "old"
    # doc meta without id stays untagged
    assert decode_meta(encode_meta({"tex_profile": "x"})) is not None


def test_backfill_tags_old_format(tmp_path):
    """Old-format `ibc1:<b64>` values get the id tag on the next write."""
    d = mkdoc()
    compile_ops(d, [PATH("p1", 10, 10, 50, 50)])
    # regress the custom value to the old untagged format
    for el in d.page.iter():
        m = get_custom(el)
        if m and m.get("id"):
            import base64, json as _json
            raw = _json.dumps(m, separators=(",", ":"),
                              sort_keys=True).encode()
            el.set("custom", "ibc1:" +
                   base64.urlsafe_b64encode(raw).decode().rstrip("="))
    compile_ops(d, [{"op": "objects.translate", "ids": ["p1"],
                     "dx": 1, "dy": 0}])
    xml = d.serialize().decode()
    assert 'custom="ibc1:p1:' in xml


# --- IMP-02/08/11: inspect geometry, apply warnings, lint ---------------------

def test_apply_surfaces_off_page_warning(tmp_path):
    p = mkdoc_file(tmp_path)
    svc = Service()
    doc = svc.open_document(str(p))
    res = apply(svc, doc["document_id"], doc["revision"], "r1",
                [PATH("p_off", 10, 290, 450, 290)])  # wider than 400bp page
    checks = [w.get("check") for w in res["warnings"]]
    assert "off_page" in checks
    off = next(w for w in res["warnings"] if w.get("check") == "off_page")
    assert off["object_id"] == "p_off"


def test_apply_warnings_include_touching_edges(tmp_path):
    p = mkdoc_file(tmp_path, w=800, h=600)
    svc = Service()
    doc = svc.open_document(str(p))
    r1 = apply(svc, doc["document_id"], doc["revision"], "r1",
               [PATH("p1", 10, 10, 50, 50), PATH("p2", 200, 10, 250, 50)])
    # move p1 fully off-page -> its warning must appear even though the edge
    # itself wasn't edited (here: no edge, just direct check)
    r2 = apply(svc, doc["document_id"], r1["revision"], "r2",
               [{"op": "objects.translate", "ids": ["p1"], "dx": 0,
                 "dy": -100}])
    ids = [w.get("object_id") for w in r2["warnings"]]
    assert "p1" in ids


def test_lint_has_frame_and_fix_hints(tmp_path):
    p = mkdoc_file(tmp_path)
    svc = Service()
    doc = svc.open_document(str(p))
    apply(svc, doc["document_id"], doc["revision"], "r1",
          [PATH("p_off", 10, 290, 380, 290)])  # fits width-wise? no: 380<400 ok
    res = apply(svc, doc["document_id"],
                svc._session(doc["document_id"]).revision, "r2",
                [PATH("p_low", 10, 350, 50, 350)])  # below page bottom
    out = svc.lint_figure(doc["document_id"])
    assert "coordinate_frame" in out and "top-left" in out["coordinate_frame"]
    findings = {(f["check"], f.get("object_id")): f for f in out["findings"]}
    off = findings.get(("off_page", "p_low"))
    assert off is not None and off.get("fix") == "fit_on_page"


def test_edge_label_overlap_check():
    """A label with no free candidate position is flagged by lint."""
    d = mkdoc(w=800, h=600)
    _, snap = compile_ops(d, [
        NODE("n1", 0, 0), NODE("n2", 200, 0),
        NODE("mid", 95, 0),  # 90-wide node blocks every label candidate
        {"op": "edge.create", "id": "e", "source": {"node": "n1"},
         "target": {"node": "n2"}, "routing": {"mode": "straight"},
         "label": {"text": "lbl", "mode": "plain"}},
    ])
    from ipe_bindcraft.routing import reroute_stale_edges
    reroute_stale_edges(d, snap)
    edge = snap.edges["e"]
    # fake measured dims so label_box is computable without TeX
    edge.label_el.set("width", "20"); edge.label_el.set("height", "6")
    assert edge.label_box(d) is not None
    out = lint(snap)
    assert any(f["check"] == "edge_label_overlap" and f["object_id"] == "e"
               and "mid" in f["message"]
               for f in out["findings"])


def test_label_avoidance_moves_off_obstacle():
    """Edge label shifts along the polyline to clear a node."""
    d = mkdoc(w=800, h=600)
    _, snap = compile_ops(d, [
        NODE("n1", 0, 0), NODE("n2", 200, 0),
        NODE("mid", 65, 0),  # covers the natural midpoint label spot
        {"op": "edge.create", "id": "e", "source": {"node": "n1"},
         "target": {"node": "n2"}, "routing": {"mode": "straight"},
         "label": {"text": "lbl", "mode": "plain"}},
    ])
    from ipe_bindcraft.routing import reroute_stale_edges
    reroute_stale_edges(d, snap)
    x_ipe = float(snap.edges["e"].label_el.get("pos").split()[0])
    # natural midpoint is ~140 (ports at ~80/~200); it must move far enough
    # to clear the 'mid' bbox x-range [65, 145]
    assert x_ipe > 150 or x_ipe < 60


# --- IMP-12: polish fixes -------------------------------------------------------

def test_polish_fit_on_page_and_wrap():
    d = mkdoc()
    _, snap = compile_ops(d, [
        PATH("p_off", 10, 350, 50, 350),  # below page bottom, nudgeable
        {"op": "text.create", "id": "note", "x": 20, "y": 50,
         "text": {"text": "some plain text", "mode": "plain"}},
    ])
    plan = polish_plan(snap, ["fit_on_page"])
    ops = [i["op"] for i in plan if i.get("op")]
    assert any(o["op"] == "objects.translate" and o["ids"] == ["p_off"]
               and o["dy"] < 0 for o in ops)

    # fake measured width so wrap_texts engages (no TeX in unit tests)
    t = snap.objects["note"]
    tel = t.text_el if t.text_el is not None else t.el
    tel.set("width", "800"); tel.set("height", "6")
    plan2 = polish_plan(snap, ["wrap_texts"])
    item = next(i for i in plan2 if i["id"] == "note")
    op = item["op"]
    assert op["op"] == "text.update" and "\n" in op["changes"]["text"]["text"]


def test_unescape_plain_and_split():
    assert _unescape_plain(r"a\_b \& c") == "a_b & c"
    assert _unescape_plain(r"\definitely{latex}") is None
    lines = _split_balanced("aa bb cc dd ee ff gg hh", 2)
    assert len(lines) == 2 and all(len(l) > 4 for l in lines)


# --- IMP-10: latest revision + status -----------------------------------------

def test_journal_summary(tmp_path):
    p = mkdoc_file(tmp_path)
    svc = Service()
    doc = svc.open_document(str(p))
    apply(svc, doc["document_id"], doc["revision"], "r1",
          [PATH("p1", 0, 0, 10, 10)])
    s = svc.journal_summary(doc["document_id"])
    assert s["requests"] == 1 and s["last_status"] == "committed"
    assert s["last_request_id"] == "r1"


# --- IMP-09: annotated preview -------------------------------------------------

def test_annotate_xml_adds_grid_and_ids(tmp_path):
    from ipe_bindcraft.export import annotate_xml
    d = mkdoc()
    compile_ops(d, [PATH("mypath", 10, 10, 50, 50)])
    out = annotate_xml(d.serialize(), grid_bp=100)
    doc2 = IpeDoc.parse(out)
    texts = [e.text for e in doc2.page.iter() if e.tag == "text"]
    paths = [e for e in doc2.page.iter() if e.tag == "path"]
    assert any("x=100" in (t or "") for t in texts)
    assert any("mypath" in (t or "") for t in texts)
    assert len(paths) >= 2 + 1  # grid lines + original path


# --- IMP-04: install-skill -----------------------------------------------------

def test_install_skill(tmp_path):
    from ipe_bindcraft.cli import _install_skill
    repo = tmp_path / "consumer"
    repo.mkdir()
    out = _install_skill(repo, "both", force=False)
    assert len(out["installed"]) == 2
    assert (repo / ".agents" / "skills" / "academic-figure" / "SKILL.md").is_file()
    assert (repo / ".devin" / "skills" / "academic-figure" / "SKILL.md").is_file()
    with pytest.raises(UsageError):
        _install_skill(repo, "agents", force=False)  # exists, no --force
    _install_skill(repo, "agents", force=True)  # overwrite ok


# --- IMP-05: schema descriptions ------------------------------------------------

def test_schema_has_coordinate_descriptions():
    from ipe_bindcraft.schemas import exported_schema_json
    s = exported_schema_json()
    assert "top-left" in s and "+y down" in s
    assert "objects.move_to" in s
