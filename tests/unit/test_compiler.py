"""Compiler tests — run without TeX (labels compile to elements; measurement
is a later pipeline stage)."""
import pytest
from pydantic import TypeAdapter

from ipe_bindcraft import styles
from ipe_bindcraft.compiler import Compiler, text_to_latex
from ipe_bindcraft.document import IpeDoc
from ipe_bindcraft.errors import OpError
from ipe_bindcraft.schemas import Operation
from ipe_bindcraft.snapshot import build_snapshot

_OPS = TypeAdapter(list[Operation])


def mkdoc(w=400, h=300):
    return IpeDoc.new(w, h, styles.stylesheet_xml("paper-default", w, h))


def compile_ops(doc, ops):
    snap = build_snapshot(doc)
    c = Compiler(doc, snap)
    res = c.apply(_OPS.validate_python(ops))
    return res, snap


def test_node_create_registers_and_positions():
    d = mkdoc()
    res, snap = compile_ops(d, [
        {"op": "node.create", "id": "n1", "shape": "rect",
         "box": {"x": 10, "y": 20, "width": 50, "height": 30},
         "label": {"text": "hi", "mode": "plain"}}])
    assert res.effects["created"] == 1
    n = snap.objects["n1"]
    bb = n.bbox(d)
    assert (bb.x, bb.y, bb.width, bb.height) == (10, 20, 50, 30)


def test_duplicate_id_fails():
    d = mkdoc()
    compile_ops(d, [{"op": "node.create", "id": "n1", "shape": "rect",
                     "box": {"x": 0, "y": 0, "width": 10, "height": 10},
                     "label": {"text": "a", "mode": "plain"}}])
    with pytest.raises(OpError) as e:
        compile_ops(d, [{"op": "node.create", "id": "n1", "shape": "rect",
                         "box": {"x": 20, "y": 20, "width": 10, "height": 10},
                         "label": {"text": "b", "mode": "plain"}}])
    assert e.value.code == "DUPLICATE_ID"


def test_edge_to_missing_node_fails():
    d = mkdoc()
    with pytest.raises(OpError) as e:
        compile_ops(d, [
            {"op": "edge.create", "id": "e1", "source": {"node": "ghost"},
             "target": {"node": "ghost2"}, "routing": {"mode": "straight"}}])
    assert e.value.code == "DANGLING_EDGE"


def test_edge_forward_reference_in_batch():
    d = mkdoc()
    res, snap = compile_ops(d, [
        {"op": "edge.create", "id": "e1", "source": {"node": "a"},
         "target": {"node": "b"}, "routing": {"mode": "straight"}},
        {"op": "node.create", "id": "a", "shape": "rect",
         "box": {"x": 0, "y": 0, "width": 10, "height": 10},
         "label": {"text": "a", "mode": "plain"}},
        {"op": "node.create", "id": "b", "shape": "rect",
         "box": {"x": 100, "y": 0, "width": 10, "height": 10},
         "label": {"text": "b", "mode": "plain"}},
    ])
    assert res.effects["created"] == 3
    assert snap.edges["e1"].needs_route


def test_delete_referenced_node_fails():
    d = mkdoc()
    compile_ops(d, [
        {"op": "node.create", "id": "a", "shape": "rect",
         "box": {"x": 0, "y": 0, "width": 10, "height": 10}, "label": {"text": "a", "mode": "plain"}},
        {"op": "node.create", "id": "b", "shape": "rect",
         "box": {"x": 50, "y": 0, "width": 10, "height": 10}, "label": {"text": "b", "mode": "plain"}},
        {"op": "edge.create", "id": "e", "source": {"node": "a"},
         "target": {"node": "b"}, "routing": {"mode": "straight"}},
    ])
    with pytest.raises(OpError) as e:
        compile_ops(d, [{"op": "objects.delete", "ids": ["a"]}])
    assert e.value.code == "REFERENCED_OBJECT"
    # cascade works
    res, _ = compile_ops(d, [{"op": "objects.delete", "ids": ["a"],
                              "cascade_edges": True}])
    assert res.effects["deleted"] == 2


def test_translate_marks_edges_stale():
    d = mkdoc()
    _, snap = compile_ops(d, [
        {"op": "node.create", "id": "a", "shape": "rect",
         "box": {"x": 0, "y": 0, "width": 10, "height": 10}, "label": {"text": "a", "mode": "plain"}},
        {"op": "node.create", "id": "b", "shape": "rect",
         "box": {"x": 50, "y": 0, "width": 10, "height": 10}, "label": {"text": "b", "mode": "plain"}},
        {"op": "edge.create", "id": "e", "source": {"node": "a"},
         "target": {"node": "b"}, "routing": {"mode": "straight"}},
    ])
    compile_ops(d, [{"op": "objects.translate", "ids": ["a"], "dx": 5, "dy": 0}])
    assert snap.edges["e"].needs_route


def test_group_ungroup_roundtrip():
    d = mkdoc()
    compile_ops(d, [
        {"op": "node.create", "id": "a", "shape": "rect",
         "box": {"x": 0, "y": 0, "width": 10, "height": 10}, "label": {"text": "a", "mode": "plain"}},
        {"op": "node.create", "id": "b", "shape": "rect",
         "box": {"x": 50, "y": 0, "width": 10, "height": 10}, "label": {"text": "b", "mode": "plain"}},
        {"op": "objects.group", "id": "g1", "ids": ["a", "b"]},
    ])
    snap = build_snapshot(d)
    assert snap.objects["g1"].member_ids == ["a", "b"]
    compile_ops(d, [{"op": "objects.ungroup", "id": "g1"}])
    snap = build_snapshot(d)
    assert "g1" not in snap.objects and "a" in snap.objects


def test_latex_escaping():
    assert text_to_latex("plain", "a & b_2") == r"a \& b\_2"
    assert text_to_latex("latex", "$x^2$") == "$x^2$"
    assert "tabular" in text_to_latex("plain", "l1\nl2")


def test_update_node_box_preserves_size_unit():
    d = mkdoc()
    compile_ops(d, [{"op": "node.create", "id": "n", "shape": "rect",
                     "box": {"x": 10, "y": 10, "width": 50, "height": 30},
                     "label": {"text": "x", "mode": "plain"}}])
    compile_ops(d, [{"op": "node.update", "id": "n",
                     "changes": {"box": {"x": 100}}}])
    snap = build_snapshot(d)
    bb = snap.objects["n"].bbox(d)
    assert (bb.x, bb.width) == (100, 50)


def test_page_escape_rejected():
    d = mkdoc()
    with pytest.raises(OpError) as e:
        compile_ops(d, [{"op": "node.create", "id": "n", "shape": "rect",
                         "box": {"x": -5, "y": 0, "width": 10, "height": 10},
                         "label": {"text": "x", "mode": "plain"}}])
    assert e.value.code == "VALIDATION"
