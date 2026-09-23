import pytest
from pydantic import TypeAdapter, ValidationError

from ipe_bindcraft.schemas import (
    ApplyOperationsInput, Operation, export_schema, finite_json_loads,
)

OPS = TypeAdapter(list[Operation])


def op(d):
    return OPS.validate_python([d])[0]


def test_node_create():
    o = op({"op": "node.create", "id": "n1", "shape": "rect",
            "box": {"x": 10, "y": 10, "width": 50, "height": 30},
            "label": {"text": "hi", "mode": "plain"}})
    assert o.id == "n1"


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        op({"op": "node.create", "id": "n1", "shape": "rect",
            "box": {"x": 0, "y": 0, "width": 1, "height": 1},
            "label": {"text": "x", "mode": "plain"}, "bogus": 1})


def test_bad_discriminator():
    with pytest.raises(ValidationError):
        op({"op": "node.explode", "id": "n1"})


def test_patch_must_be_nonempty():
    with pytest.raises(ValidationError):
        op({"op": "node.update", "id": "n1", "changes": {}})


def test_explicit_null_label_is_a_change():
    o = op({"op": "edge.update", "id": "e1", "changes": {"label": None}})
    assert "label" in o.changes.model_fields_set


def test_port_offset_rejected_on_auto():
    with pytest.raises(ValidationError):
        op({"op": "edge.create", "id": "e1",
            "source": {"node": "a", "port": {"side": "auto", "offset": 0.5}},
            "target": {"node": "b"}, "routing": {"mode": "straight"}})


def test_radius_only_on_rounded():
    with pytest.raises(ValidationError):
        op({"op": "node.create", "id": "n1", "shape": "rect",
            "box": {"x": 0, "y": 0, "width": 1, "height": 1},
            "label": {"text": "x", "mode": "plain"}, "corner_radius_bp": 3})


def test_nonfinite_rejected():
    with pytest.raises(ValidationError):
        op({"op": "objects.translate", "ids": ["a"], "dx": float("nan"), "dy": 0})
    with pytest.raises(ValueError):
        finite_json_loads('{"x": NaN}')


def test_segment_must_start_with_move():
    with pytest.raises(ValidationError):
        op({"op": "path.create", "id": "p1",
            "segments": [{"cmd": "L", "x": 5, "y": 5},
                         {"cmd": "L", "x": 9, "y": 9}]})


def test_apply_input_requires_min_ops():
    with pytest.raises(ValidationError):
        ApplyOperationsInput(document_id="d", expected_revision="r",
                             request_id="q", operations=[])


def test_exported_schema_is_object():
    s = export_schema()
    assert s["type"] == "object"
    assert "operations" in s["properties"]
    assert "$defs" in s
