"""Generate the three M2 fixtures through the real service pipeline.

Usage:  python scripts/make_fixtures.py [outdir]
Writes: <outdir>/system_overview.ipe, algorithm_pipeline.ipe, parallel_workers.ipe
Each fixture is built with create_document + apply_operations, so every file
exercises the full compile->reroute->measure->commit path. The operation
batches live in ipe_bindcraft.templates (single source of truth shared with
create_document(template=...)).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ipe_bindcraft.schemas import ApplyOperationsInput, Operation
from ipe_bindcraft.service import Service
from ipe_bindcraft.templates import DESIGN_H, DESIGN_W, render_template
from pydantic import TypeAdapter

_OPS = TypeAdapter(list[Operation])


def build(svc: Service, path: Path, template: str) -> dict:
    info = svc.create_document(str(path), DESIGN_W, DESIGN_H, "paper-default")
    inp = ApplyOperationsInput(
        document_id=info["document_id"], expected_revision=info["revision"],
        request_id=f"fixture-{path.stem}",
        operations=_OPS.validate_python(
            render_template(template, DESIGN_W, DESIGN_H)))
    res = svc.apply_operations(inp)
    res["path"] = str(path)
    return res


def main():
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tests/fixtures")
    outdir.mkdir(parents=True, exist_ok=True)
    svc = Service()
    for name in ("system_overview", "algorithm_pipeline", "parallel_workers"):
        res = build(svc, outdir / f"{name}.ipe", name)
        print(f"{name}: rev={res['revision'][:24]} effects={res['effects']}")
        print(f"  -> {res['path']}")


if __name__ == "__main__":
    main()
