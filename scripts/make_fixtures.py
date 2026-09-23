"""Generate the three M2 fixtures through the real service pipeline.

Usage:  python scripts/make_fixtures.py [outdir]
Writes: <outdir>/system_overview.ipe, algorithm_pipeline.ipe, parallel_workers.ipe
Each fixture is built with create_document + apply_operations, so every file
exercises the full compile->reroute->measure->commit path.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ipe_bindcraft.schemas import ApplyOperationsInput, Operation
from ipe_bindcraft.service import Service
from pydantic import TypeAdapter

_OPS = TypeAdapter(list[Operation])

W, H = 460.0, 300.0


def build(svc: Service, path: Path, ops: list[dict]) -> dict:
    info = svc.create_document(str(path), W, H, "paper-default")
    inp = ApplyOperationsInput(
        document_id=info["document_id"], expected_revision=info["revision"],
        request_id=f"fixture-{path.stem}", operations=_OPS.validate_python(ops))
    res = svc.apply_operations(inp)
    res["path"] = str(path)
    return res


def n(oid, x, y, w, h, label, shape="rect", role="data", mode="plain"):
    return {"op": "node.create", "id": oid, "shape": shape,
            "box": {"x": x, "y": y, "width": w, "height": h},
            "label": {"text": label, "mode": mode}, "role": role}


def e(oid, a, b, sa="east", sb="west", mode="straight", label=None):
    op = {"op": "edge.create", "id": oid,
          "source": {"node": a, "port": {"side": sa}},
          "target": {"node": b, "port": {"side": sb}},
          "routing": {"mode": mode}}
    if label:
        op["label"] = {"text": label, "mode": "plain"}
    return op


def system_overview() -> list[dict]:
    """Eight-node system diagram: input -> 2x2 pipeline -> output."""
    ops = [
        n("src", 20, 130, 60, 30, "Input", "rect", "io"),
        n("parse", 110, 40, 70, 30, "Parser", "rounded_rect", "compute"),
        n("valid", 110, 130, 70, 30, "Validator", "rounded_rect", "compute"),
        n("store", 210, 40, 70, 30, "Storage", "rect", "memory"),
        n("cache", 210, 130, 70, 30, "Cache", "rect", "memory"),
        n("agg", 310, 85, 70, 30, "Aggregator", "diamond", "control"),
        n("audit", 210, 220, 70, 30, "Audit Log", "rect", "data"),
        n("out", 395, 85, 50, 30, "Out", "ellipse", "io"),
        e("e1", "src", "parse", "east", "west"),
        e("e2", "src", "valid", "east", "west"),
        e("e3", "parse", "store", "east", "west"),
        e("e4", "valid", "cache", "east", "west"),
        e("e5", "store", "agg", "east", "west"),
        e("e6", "cache", "agg", "east", "west"),
        e("e7", "agg", "out", "east", "west"),
        e("e8", "valid", "audit", "south", "north", "orthogonal"),
    ]
    return ops


def algorithm_pipeline() -> list[dict]:
    """Formula/feedback algorithm diagram: loop back edge + math labels."""
    ops = [
        n("init", 20, 120, 80, 36, "Initialize $x_0$", "rect", "data", "latex"),
        n("step", 140, 120, 90, 36, "Iterate $x_{k+1}$", "rounded_rect", "compute", "latex"),
        n("test", 270, 120, 80, 36, "$|r| < \\epsilon$?", "diamond", "control", "latex"),
        n("done", 385, 120, 60, 36, "Done", "ellipse", "io"),
        n("refine", 140, 220, 90, 30, "Refine mesh", "rect", "memory"),
        e("f1", "init", "step"),
        e("f2", "step", "test", label="r"),
        e("f3", "test", "done", label="yes"),
        # feedback loop: test.south -> refine -> step.south (manual waypoint)
        {"op": "edge.create", "id": "f4",
         "source": {"node": "test", "port": {"side": "south"}},
         "target": {"node": "refine", "port": {"side": "east"}},
         "routing": {"mode": "manual", "waypoints": [{"x": 310, "y": 235}]}},
        e("f5", "refine", "step", "west", "south", "orthogonal", label="no"),
    ]
    return ops


def parallel_workers() -> list[dict]:
    """Four-worker parallel diagram: dispatcher -> 4 workers -> join."""
    ops = [
        n("disp", 20, 150, 70, 30, "Dispatcher", "rounded_rect", "control"),
        n("w1", 140, 40, 80, 28, "Worker 1", "rect", "compute"),
        n("w2", 140, 95, 80, 28, "Worker 2", "rect", "compute"),
        n("w3", 140, 150, 80, 28, "Worker 3", "rect", "compute"),
        n("w4", 140, 205, 80, 28, "Worker 4", "rect", "compute"),
        n("join", 300, 150, 60, 30, "Join", "diamond", "control"),
        n("sink", 400, 150, 45, 30, "Sink", "ellipse", "io"),
    ]
    for i in range(1, 5):
        ops.append(e(f"d{i}", "disp", f"w{i}"))
        ops.append(e(f"j{i}", f"w{i}", "join"))
    ops.append(e("out", "join", "sink"))
    return ops


def main():
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tests/fixtures")
    outdir.mkdir(parents=True, exist_ok=True)
    svc = Service()
    for name, ops in (("system_overview", system_overview),
                      ("algorithm_pipeline", algorithm_pipeline),
                      ("parallel_workers", parallel_workers)):
        res = build(svc, outdir / f"{name}.ipe", ops())
        print(f"{name}: rev={res['revision'][:24]} effects={res['effects']}")
        print(f"  -> {res['path']}")


if __name__ == "__main__":
    main()
