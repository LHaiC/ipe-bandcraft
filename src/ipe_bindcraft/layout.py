"""Layout helpers: align / distribute / grid.

Each function returns ``{id: (dx, dy)}`` in API space; the caller applies the
deltas through the normal compile pipeline (objects.translate) so revision,
locking, and edge invalidation stay consistent.
"""

from __future__ import annotations

from .coordinates import Box, Point
from .errors import IbcError
from .snapshot import SceneSnapshot


def _boxes(snap: SceneSnapshot, ids: list[str]) -> dict[str, Box]:
    out = {}
    for oid in ids:
        obj = snap.get(oid)
        if obj is None:
            raise IbcError("NOT_FOUND", f"object {oid!r} not found", details={"id": oid})
        if getattr(obj, "unsupported_transform", False):
            raise IbcError(
                "UNSUPPORTED_TRANSFORM",
                f"{oid!r} carries a non-translation transform; layout is undefined",
                details={"id": oid},
            )
        bb = obj.bbox(snap.doc)
        if bb is None:
            raise IbcError(
                "CONSTRAINT_UNSATISFIABLE",
                f"{oid!r} has no measurable bounding box (unmeasured text?)",
                details={"id": oid},
            )
        out[oid] = bb
    return out


def compute_align(snap: SceneSnapshot, ids: list[str], options: dict | None) -> dict[str, tuple[float, float]]:
    """options: {axis: left|center_x|right|top|center_y|bottom, to: selection|page}"""
    opts = options or {}
    axis = opts.get("axis", "left")
    boxes = _boxes(snap, ids)
    if not boxes:
        return {}
    if opts.get("to") == "page":
        ref_x1, ref_y1 = 0.0, 0.0
        ref_x2, ref_y2 = snap.page_w, snap.page_h
    else:
        ref_x1 = min(b.x for b in boxes.values())
        ref_y1 = min(b.y for b in boxes.values())
        ref_x2 = max(b.x2 for b in boxes.values())
        ref_y2 = max(b.y2 for b in boxes.values())

    deltas: dict[str, tuple[float, float]] = {}
    for oid, b in boxes.items():
        if axis == "left":
            dx, dy = ref_x1 - b.x, 0.0
        elif axis == "center_x":
            dx, dy = (ref_x1 + ref_x2) / 2 - b.cx, 0.0
        elif axis == "right":
            dx, dy = ref_x2 - b.x2, 0.0
        elif axis == "top":
            dx, dy = 0.0, ref_y1 - b.y
        elif axis == "center_y":
            dx, dy = 0.0, (ref_y1 + ref_y2) / 2 - b.cy
        elif axis == "bottom":
            dx, dy = 0.0, ref_y2 - b.y2
        else:
            raise IbcError("VALIDATION", f"unknown align axis {axis!r}")
        if dx or dy:
            deltas[oid] = (dx, dy)
    return deltas


def compute_distribute(snap: SceneSnapshot, ids: list[str], options: dict | None) -> dict[str, tuple[float, float]]:
    """Even spacing along an axis. options: {axis: x|y, gap_bp?: float}.

    With no gap, centers are distributed evenly between the extremes.
    With gap_bp, boxes are packed left-to-right/top-to-bottom starting at the
    min edge; raises CONSTRAINT_UNSATISFIABLE if they cannot fit the current span.
    """
    opts = options or {}
    axis = opts.get("axis", "x")
    gap = opts.get("gap_bp")
    boxes = _boxes(snap, ids)
    if len(boxes) < 3 and gap is None:
        return {}

    key = (lambda b: b.cx) if axis == "x" else (lambda b: b.cy)
    order = sorted(boxes.items(), key=lambda kv: key(kv[1]))
    first_b, last_b = order[0][1], order[-1][1]

    deltas: dict[str, tuple[float, float]] = {}
    if gap is None:
        lo, hi = key(first_b), key(last_b)
        n = len(order)
        for i, (oid, b) in enumerate(order):
            target = lo + (hi - lo) * i / (n - 1)
            d = target - key(b)
            if d:
                deltas[oid] = (d, 0.0) if axis == "x" else (0.0, d)
    else:
        span = (last_b.x2 - first_b.x) if axis == "x" else (last_b.y2 - first_b.y)
        sizes = [b.width if axis == "x" else b.height for _, b in order]
        need = sum(sizes) + gap * (len(order) - 1)
        if need > span + 0.01:
            raise IbcError(
                "CONSTRAINT_UNSATISFIABLE",
                f"cannot fit {len(order)} objects with gap {gap}bp into span {span:.1f}bp",
                details={"need_bp": need, "span_bp": span},
            )
        cursor = first_b.x if axis == "x" else first_b.y
        for oid, b in order:
            start = b.x if axis == "x" else b.y
            d = cursor - start
            if d:
                deltas[oid] = (d, 0.0) if axis == "x" else (0.0, d)
            cursor += (b.width if axis == "x" else b.height) + gap
    return deltas


def compute_grid(snap: SceneSnapshot, ids: list[str], options: dict | None) -> dict[str, tuple[float, float]]:
    """Grid packing. options: {columns:int, h_gap_bp, v_gap_bp, origin:{x,y}?}"""
    opts = options or {}
    cols = int(opts.get("columns") or max(1, round(len(ids) ** 0.5)))
    hgap = float(opts.get("h_gap_bp", 24.0))
    vgap = float(opts.get("v_gap_bp", 24.0))
    boxes = _boxes(snap, ids)

    origin = opts.get("origin")
    if origin:
        ox, oy = float(origin["x"]), float(origin["y"])
    else:
        ox = min(b.x for b in boxes.values())
        oy = min(b.y for b in boxes.values())

    rows = (len(ids) + cols - 1) // cols
    col_w = [0.0] * cols
    row_h = [0.0] * rows
    for i, oid in enumerate(ids):
        r, c = divmod(i, cols)
        b = boxes[oid]
        col_w[c] = max(col_w[c], b.width)
        row_h[r] = max(row_h[r], b.height)

    deltas: dict[str, tuple[float, float]] = {}
    x_starts = [ox]
    for c in range(1, cols):
        x_starts.append(x_starts[-1] + col_w[c - 1] + hgap)
    y_starts = [oy]
    for r in range(1, rows):
        y_starts.append(y_starts[-1] + row_h[r - 1] + vgap)
    for i, oid in enumerate(ids):
        r, c = divmod(i, cols)
        b = boxes[oid]
        dx, dy = x_starts[c] - b.x, y_starts[r] - b.y
        if dx or dy:
            deltas[oid] = (dx, dy)
    return deltas


def compute_layout(snap: SceneSnapshot, ids: list[str], mode: str,
                   options: dict | None) -> dict[str, tuple[float, float]]:
    if mode == "align":
        return compute_align(snap, ids, options)
    if mode == "distribute":
        return compute_distribute(snap, ids, options)
    if mode == "grid":
        return compute_grid(snap, ids, options)
    raise IbcError("VALIDATION", f"unknown layout mode {mode!r}")
