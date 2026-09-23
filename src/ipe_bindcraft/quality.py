"""Lint and safe-polish for a figure document.

Lint findings use ``status``: one of ``pass``, ``warn``, ``fail``,
``not_checked``. ``not_checked`` means the check could not be evaluated (e.g.
text not measured yet) — it is never reported as ``pass``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .coordinates import Box
from .errors import IbcError
from .snapshot import EdgeObj, NodeObj, PathObj, SceneSnapshot, TextObj


@dataclass
class Finding:
    check: str
    status: str            # pass | warn | fail | not_checked
    object_id: str | None = None
    message: str = ""
    details: dict = field(default_factory=dict)


def lint(snap: SceneSnapshot, target_width_bp: float | None = None) -> dict:
    findings: list[Finding] = []

    # duplicate ids
    for did, els in snap.duplicates.items():
        findings.append(Finding(
            "duplicate_id", "fail", did,
            f"id {did!r} appears {len(els)} times; ambiguous identity",
        ))

    for oid, obj in snap.objects.items():
        bb = obj.bbox(snap.doc)

        # off-page
        if bb is not None and not bb.inside_page(snap.page_w, snap.page_h, margin=-0.5):
            findings.append(Finding(
                "off_page", "warn", oid,
                f"object extends outside the page: {bb}",
                {"box": [bb.x, bb.y, bb.width, bb.height]},
            ))

        # unsupported transform
        if getattr(obj, "unsupported_transform", False):
            findings.append(Finding(
                "unsupported_transform", "warn", oid,
                "object has a non-translation transform; edits may be refused",
            ))

        if isinstance(obj, NodeObj):
            # label overflow: measured label box vs body box
            lb = obj.label_box(snap.doc)
            if lb is None:
                findings.append(Finding(
                    "text_overflow", "not_checked", oid,
                    "label size unknown (LaTeX not run for this object)",
                ))
            elif bb is not None:
                pad = 2.0
                if (lb.width + 2 * pad > bb.width or lb.height + 2 * pad > bb.height):
                    findings.append(Finding(
                        "text_overflow", "warn", oid,
                        f"label box {lb.width:.1f}x{lb.height:.1f}bp exceeds node "
                        f"{bb.width:.1f}x{bb.height:.1f}bp",
                        {"label": [lb.x, lb.y, lb.width, lb.height]},
                    ))
                else:
                    findings.append(Finding("text_overflow", "pass", oid, ""))

        elif isinstance(obj, TextObj):
            if bb is None:
                findings.append(Finding(
                    "text_measure", "not_checked", oid,
                    "text size unknown (LaTeX not run)",
                ))

        elif isinstance(obj, EdgeObj):
            if obj.needs_route:
                findings.append(Finding(
                    "stale_route", "warn", oid, "edge geometry is stale (needs_route)")
                )
            for ep_name, ep in (("source", obj.source), ("target", obj.target)):
                nid = (ep or {}).get("node")
                if nid and nid not in snap.objects:
                    findings.append(Finding(
                        "dangling_edge", "fail", oid,
                        f"{ep_name} node {nid!r} does not exist",
                    ))

    # node-node overlap (visual cleanliness; warn only)
    nodes = [(o.id, o.bbox(snap.doc)) for o in snap.objects.values()
             if isinstance(o, NodeObj)]
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            a, b = nodes[i][1], nodes[j][1]
            if a and b and a.intersects(b):
                findings.append(Finding(
                    "node_overlap", "warn", nodes[i][0],
                    f"overlaps node {nodes[j][0]!r}",
                ))

    # target width: scale check for the whole figure
    scale_note = None
    if target_width_bp:
        xs = [o.bbox(snap.doc) for o in snap.objects.values()]
        boxes = [b for b in xs if b]
        if boxes:
            w = max(b.x2 for b in boxes) - min(b.x for b in boxes)
            if w > 0:
                scale_note = {
                    "figure_width_bp": w,
                    "target_width_bp": target_width_bp,
                    "scale": target_width_bp / w,
                    "min_font_bp_at_target": None,  # filled by caller if known
                }

    summary = {"pass": 0, "warn": 0, "fail": 0, "not_checked": 0}
    for f in findings:
        summary[f.status] += 1
    return {
        "findings": [f.__dict__ for f in findings],
        "summary": summary,
        "scale": scale_note,
    }


# --- safe polish ---------------------------------------------------------------

SAFE_FIXES = {
    "recenter_labels",      # move node labels back to body center
    "clear_stale_routes",   # drop needs_route flags (they get rerouted anyway)
    "grow_nodes_to_label",  # enlarge node box to fit its label (no font shrink)
}


def polish_plan(snap: SceneSnapshot, fixes: list[str]) -> list[dict]:
    """Compute a plan of safe fixes. No mutation — returns proposed ops."""
    unknown = set(fixes) - SAFE_FIXES
    if unknown:
        raise IbcError("VALIDATION", f"unknown polish fixes: {sorted(unknown)}",
                       details={"known": sorted(SAFE_FIXES)})
    plan: list[dict] = []
    for fix in fixes:
        if fix == "recenter_labels":
            for oid, obj in snap.objects.items():
                if not isinstance(obj, NodeObj) or obj.label is None or obj.body is None:
                    continue
                bb = obj.bbox(snap.doc)
                if bb is None:
                    continue
                lb = obj.label_box(snap.doc)
                if lb is None:
                    continue
                dx, dy = bb.cx - lb.cx, bb.cy - lb.cy
                if abs(dx) > 0.5 or abs(dy) > 0.5:
                    plan.append({
                        "fix": fix, "id": oid,
                        "op": {"op": "objects.translate", "ids": [], "dx": 0, "dy": 0},
                        "detail": f"move label by ({dx:.1f},{dy:.1f}) to node center",
                        "delta": [dx, dy],
                    })
        elif fix == "grow_nodes_to_label":
            for oid, obj in snap.objects.items():
                if not isinstance(obj, NodeObj) or obj.body is None:
                    continue
                bb, lb = obj.bbox(snap.doc), obj.label_box(snap.doc)
                if bb is None or lb is None:
                    continue
                pad = 4.0
                if lb.width + 2 * pad > bb.width or lb.height + 2 * pad > bb.height:
                    nw = max(bb.width, lb.width + 2 * pad)
                    nh = max(bb.height, lb.height + 2 * pad)
                    plan.append({
                        "fix": fix, "id": oid,
                        "detail": f"grow node {bb.width:.0f}x{bb.height:.0f} -> "
                                  f"{nw:.0f}x{nh:.0f} (font unchanged)",
                        "box": [bb.cx - nw / 2, bb.cy - nh / 2, nw, nh],
                    })
        elif fix == "clear_stale_routes":
            for oid, obj in snap.edges.items():
                if obj.needs_route:
                    plan.append({"fix": fix, "id": oid,
                                 "detail": "edge will be rerouted on next apply"})
    return plan
