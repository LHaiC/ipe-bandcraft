"""Lint and safe-polish for a figure document.

Lint findings use ``status``: one of ``pass``, ``warn``, ``fail``,
``not_checked``. ``not_checked`` means the check could not be evaluated (e.g.
text not measured yet) — it is never reported as ``pass``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .coordinates import Box
from .errors import IbcError
from .snapshot import EdgeObj, NodeObj, PathObj, SceneSnapshot, TextObj

API_FRAME = "API coords: origin top-left, +x right, +y down, units bp"


@dataclass
class Finding:
    check: str
    status: str            # pass | warn | fail | not_checked
    object_id: str | None = None
    message: str = ""
    details: dict = field(default_factory=dict)
    fix: str | None = None  # a SAFE_FIXES name, when one exists

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()
             if not (k == "fix" and v is None)}
        return d


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
            fit = (bb.width <= snap.page_w and bb.height <= snap.page_h)
            fix = None
            if isinstance(obj, EdgeObj):
                pass  # edges follow their endpoints; nudging is meaningless
            elif fit:
                fix = "fit_on_page"
            elif isinstance(obj, TextObj) and obj.meta.get("mode", "plain") == "plain":
                fix = "wrap_texts"
            findings.append(Finding(
                "off_page", "warn", oid,
                f"object extends outside the page: {bb} ({API_FRAME})",
                {"box": [bb.x, bb.y, bb.width, bb.height]},
                fix=fix,
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
                        f"label {lb.width:.1f}x{lb.height:.1f}bp + {2*pad:.0f}bp "
                        f"padding needs ~{lb.width + 2*pad:.1f}x"
                        f"{lb.height + 2*pad:.1f}bp; node box is "
                        f"{bb.width:.1f}x{bb.height:.1f}bp",
                        {"label": [lb.x, lb.y, lb.width, lb.height]},
                        fix="grow_nodes_to_label",
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
                    "stale_route", "warn", oid, "edge geometry is stale (needs_route)",
                    fix="clear_stale_routes"))
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
                    {"with": nodes[j][0]},
                ))

    # edge labels colliding with nodes / other edge labels
    label_boxes: list[tuple[str, Box]] = []
    for eid, edge in snap.edges.items():
        lb = edge.label_box(snap.doc)
        if lb is None:
            continue
        label_boxes.append((eid, lb))
        hits = [oid for oid, nb in nodes if nb is not None and lb.intersects(nb)]
        others = [o2 for o2, b2 in label_boxes[:-1] if lb.intersects(b2)]
        if hits or others:
            what = [f"node {h!r}" for h in hits] + \
                   [f"label of {o!r}" for o in others]
            findings.append(Finding(
                "edge_label_overlap", "warn", eid,
                f"edge label collides with {', '.join(what)}",
                {"label": [lb.x, lb.y, lb.width, lb.height]},
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
        "findings": [f.as_dict() for f in findings],
        "summary": summary,
        "scale": scale_note,
        "coordinate_frame": API_FRAME,
    }


# --- safe polish ---------------------------------------------------------------

SAFE_FIXES = {
    "recenter_labels",      # move node labels back to body center (plan only)
    "clear_stale_routes",   # drop needs_route flags (they get rerouted anyway)
    "grow_nodes_to_label",  # enlarge node box to fit its label (no font shrink)
    "fit_on_page",          # nudge objects back inside the page bounds
    "wrap_texts",           # rewrap over-wide single-line plain texts
}

_LATEX_UNESCAPES = {
    r"\textasciitilde{}": "~",
    r"\textasciicircum{}": "^",
    r"\&": "&", r"\%": "%", r"\$": "$", r"\#": "#",
    r"\_": "_", r"\{": "{", r"\}": "}",
}


def _unescape_plain(ltx: str) -> str | None:
    """Recover the raw text of a single-paragraph plain-mode label, or None
    if the content is not recoverable (e.g. real LaTeX)."""
    s = ltx.strip()
    m = re.match(r"^\\begin\{tabular\}\{[^}]*\}(.*)\\end\{tabular\}$", s, re.S)
    if m:
        s = m.group(1)
        s = re.sub(r"\s*\\\\\s*", "\n", s)  # row separators -> newlines
    s = s.replace(r"\textbackslash{}", "\x00")  # sentinel: real backslashes last
    for k, v in _LATEX_UNESCAPES.items():
        s = s.replace(k, v)
    if "\\" in s.replace("\x00", ""):
        return None  # leftover LaTeX command — don't touch
    return s.replace("\x00", "\\")


def _split_balanced(text: str, n: int) -> list[str]:
    """Split text into <= n lines at word boundaries, roughly equal length."""
    words = text.split()
    if not words:
        return [text]
    target = max(1, math.ceil(sum(len(w) + 1 for w in words) / n))
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + len(w) + 1 > target and len(lines) < n - 1:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    if cur:
        lines.append(cur)
    return lines


def polish_plan(snap: SceneSnapshot, fixes: list[str]) -> list[dict]:
    """Compute a plan of safe fixes. No mutation — returns proposed ops.

    Items that can be expressed as a typed op carry an ``op`` dict; plan-only
    items (e.g. label-only moves) carry ``op: None`` plus a ``detail`` note.
    """
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
                        "fix": fix, "id": oid, "op": None,
                        "detail": f"move label by ({dx:.1f},{dy:.1f}) to node center "
                                  f"(label-only moves are not expressible as an op)",
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
                    box = {"x": bb.cx - nw / 2, "y": bb.cy - nh / 2,
                           "width": nw, "height": nh}
                    plan.append({
                        "fix": fix, "id": oid,
                        "op": {"op": "node.update", "id": oid,
                               "changes": {"box": box}},
                        "detail": f"grow node {bb.width:.0f}x{bb.height:.0f} -> "
                                  f"{nw:.0f}x{nh:.0f} (font unchanged)",
                        "box": [box["x"], box["y"], box["width"], box["height"]],
                    })
        elif fix == "clear_stale_routes":
            for oid, obj in snap.edges.items():
                if obj.needs_route:
                    plan.append({"fix": fix, "id": oid, "op": None,
                                 "detail": "edge will be rerouted on next apply"})
        elif fix == "fit_on_page":
            W, H = snap.page_w, snap.page_h
            for oid, obj in snap.objects.items():
                if isinstance(obj, EdgeObj):
                    continue  # edges follow their endpoints; endpoints get moved
                bb = obj.bbox(snap.doc)
                if bb is None or bb.inside_page(W, H, margin=-0.5):
                    continue
                if bb.width > W or bb.height > H:
                    continue  # too big to nudge — see wrap_texts
                dx = (-bb.x if bb.x < 0 else
                      W - bb.x2 if bb.x2 > W else 0.0)
                dy = (-bb.y if bb.y < 0 else
                      H - bb.y2 if bb.y2 > H else 0.0)
                plan.append({
                    "fix": fix, "id": oid,
                    "op": {"op": "objects.translate", "ids": [oid],
                           "dx": dx, "dy": dy},
                    "detail": f"nudge by ({dx:.1f},{dy:.1f}) back onto page",
                })
        elif fix == "wrap_texts":
            W = snap.page_w
            for oid, obj in snap.objects.items():
                if not isinstance(obj, TextObj):
                    continue
                bb = obj.bbox(snap.doc)
                if bb is None or bb.width <= W - bb.x - 2:
                    continue  # fits or unmeasured
                if obj.meta.get("mode", "plain") != "plain":
                    plan.append({"fix": fix, "id": oid, "op": None,
                                 "detail": "latex-mode text too wide; "
                                           "rewrap manually"})
                    continue
                raw = _unescape_plain(obj.content)
                if raw is None or "\n" in raw:
                    plan.append({"fix": fix, "id": oid, "op": None,
                                 "detail": "text not rewrappable; "
                                           "edit content manually"})
                    continue
                avail = max(20.0, W - bb.x - 4)
                n = math.ceil(bb.width / avail)
                lines = _split_balanced(raw, n)
                if len(lines) < 2:
                    continue
                plan.append({
                    "fix": fix, "id": oid,
                    "op": {"op": "text.update", "id": oid,
                           "changes": {"text": {"text": "\n".join(lines)}}},
                    "detail": f"wrap into {len(lines)} lines "
                              f"(~{avail:.0f}bp available)",
                })
    return plan
