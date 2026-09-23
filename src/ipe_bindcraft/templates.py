"""Starter templates: canned operation batches instantiated by create_document.

Each template is authored at a fixed design size and linearly scaled onto the
target page. They are example layouts, not official journal standards.
"""

from __future__ import annotations

from typing import Callable

# design canvas the templates were authored on (bp)
DESIGN_W, DESIGN_H = 460.0, 300.0


def _n(oid, x, y, w, h, label, shape="rect", role="data", mode="plain") -> dict:
    return {"op": "node.create", "id": oid, "shape": shape,
            "box": {"x": x, "y": y, "width": w, "height": h},
            "label": {"text": label, "mode": mode}, "role": role}


def _e(oid, a, b, sa="east", sb="west", mode="straight", label=None) -> dict:
    op = {"op": "edge.create", "id": oid,
          "source": {"node": a, "port": {"side": sa}},
          "target": {"node": b, "port": {"side": sb}},
          "routing": {"mode": mode}}
    if label:
        op["label"] = {"text": label, "mode": "plain"}
    return op


def _system_overview() -> list[dict]:
    """Eight-node system diagram: input -> 2x2 pipeline -> output."""
    return [
        _n("src", 20, 130, 60, 30, "Input", "rect", "io"),
        _n("parse", 110, 40, 70, 30, "Parser", "rounded_rect", "compute"),
        _n("valid", 110, 130, 70, 30, "Validator", "rounded_rect", "compute"),
        _n("store", 210, 40, 70, 30, "Storage", "rect", "memory"),
        _n("cache", 210, 130, 70, 30, "Cache", "rect", "memory"),
        _n("agg", 310, 85, 70, 30, "Aggregator", "diamond", "control"),
        _n("audit", 210, 220, 70, 30, "Audit Log", "rect", "data"),
        _n("out", 395, 85, 50, 30, "Out", "ellipse", "io"),
        _e("e1", "src", "parse"), _e("e2", "src", "valid"),
        _e("e3", "parse", "store"), _e("e4", "valid", "cache"),
        _e("e5", "store", "agg"), _e("e6", "cache", "agg"),
        _e("e7", "agg", "out"),
        _e("e8", "valid", "audit", "south", "north", "orthogonal"),
    ]


def _algorithm_pipeline() -> list[dict]:
    """Formula/feedback algorithm diagram: loop back edge + math labels."""
    return [
        _n("init", 20, 120, 80, 36, "Initialize $x_0$", "rect", "data", "latex"),
        _n("step", 140, 120, 90, 36, "Iterate $x_{k+1}$", "rounded_rect", "compute", "latex"),
        _n("test", 270, 120, 80, 36, "$|r| < \\epsilon$?", "diamond", "control", "latex"),
        _n("done", 385, 120, 60, 36, "Done", "ellipse", "io"),
        _n("refine", 140, 220, 90, 30, "Refine mesh", "rect", "memory"),
        _e("f1", "init", "step"),
        _e("f2", "step", "test", label="r"),
        _e("f3", "test", "done", label="yes"),
        {"op": "edge.create", "id": "f4",
         "source": {"node": "test", "port": {"side": "south"}},
         "target": {"node": "refine", "port": {"side": "east"}},
         "routing": {"mode": "manual", "waypoints": [{"x": 310, "y": 235}]}},
        _e("f5", "refine", "step", "west", "south", "orthogonal", label="no"),
    ]


def _parallel_workers() -> list[dict]:
    """Four-worker parallel diagram: dispatcher -> 4 workers -> join."""
    ops = [
        _n("disp", 20, 150, 70, 30, "Dispatcher", "rounded_rect", "control"),
        _n("w1", 140, 40, 80, 28, "Worker 1", "rect", "compute"),
        _n("w2", 140, 95, 80, 28, "Worker 2", "rect", "compute"),
        _n("w3", 140, 150, 80, 28, "Worker 3", "rect", "compute"),
        _n("w4", 140, 205, 80, 28, "Worker 4", "rect", "compute"),
        _n("join", 300, 150, 60, 30, "Join", "diamond", "control"),
        _n("sink", 400, 150, 45, 30, "Sink", "ellipse", "io"),
    ]
    for i in range(1, 5):
        ops.append(_e(f"d{i}", "disp", f"w{i}"))
        ops.append(_e(f"j{i}", f"w{i}", "join"))
    ops.append(_e("out", "join", "sink"))
    return ops


TEMPLATES: dict[str, Callable[[], list[dict]]] = {
    "system_overview": _system_overview,
    "algorithm_pipeline": _algorithm_pipeline,
    "parallel_workers": _parallel_workers,
}


def _scale_ops(ops: list[dict], sx: float, sy: float) -> list[dict]:
    out = []
    for op in ops:
        op = dict(op)
        if "box" in op:
            b = dict(op["box"])
            b["x"], b["y"] = b["x"] * sx, b["y"] * sy
            b["width"], b["height"] = b["width"] * sx, b["height"] * sy
            op["box"] = b
        wp = op.get("routing", {}).get("waypoints") if isinstance(op.get("routing"), dict) else None
        if wp:
            op["routing"] = dict(op["routing"],
                                 waypoints=[{"x": p["x"] * sx, "y": p["y"] * sy}
                                            for p in wp])
        out.append(op)
    return out


def render_template(name: str, page_w: float, page_h: float) -> list[dict]:
    from .errors import IbcError
    fn = TEMPLATES.get(name)
    if fn is None:
        raise IbcError("VALIDATION", f"unknown template {name!r}",
                       details={"known": sorted(TEMPLATES)})
    ops = fn()
    sx, sy = page_w / DESIGN_W, page_h / DESIGN_H
    if abs(sx - 1.0) > 1e-9 or abs(sy - 1.0) > 1e-9:
        ops = _scale_ops(ops, sx, sy)
    return ops
