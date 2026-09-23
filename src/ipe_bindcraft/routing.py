"""Edge routing: resolve semantic endpoints to outline ports, then route.

Supported modes (spec-limited, honest scope):
- straight: source port -> [waypoints] -> target port
- orthogonal: axis-aligned via a single bend channel, avoids crossing endpoint
  node boxes when a simple channel exists; falls back to straight with a
  warning when no clean channel exists.
- manual: waypoints verbatim between resolved ports.

Ports are real outline intersection points (geometry.port_point), not bbox
centers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .coordinates import Box, Point, fmt, to_ipe
from .errors import OpError
from . import geometry as geo
from .snapshot import EdgeObj, NodeObj, SceneSnapshot

GAP = 8.0  # bp clearance from node outline for orthogonal channels


@dataclass
class RouteResult:
    points: list[Point]  # API space polyline incl. endpoints
    warnings: list[str] = field(default_factory=list)


def _port(node: NodeObj, subs, bb: Box, side: str, offset: float | None) -> Point:
    bb_t = (bb.x, bb.y, bb.x2, bb.y2)
    x, y = geo.port_point(subs, bb_t, side, offset)
    return Point(x, y)


def resolve_port(node: NodeObj, side: str, offset: float | None,
                 snap: SceneSnapshot, toward: Point | None = None) -> Point:
    """Real outline port on the node in API space."""
    subs = node.subpaths(snap.doc)
    bb = node.bbox(snap.doc)
    if bb is None:
        raise OpError("INVALID_OPERATION", f"node {node.id!r} has no measurable outline",
                      object_ids=[node.id])
    if side == "auto":
        # pick the side whose port is closest to `toward` (or target center)
        best, best_d = None, None
        for s in ("north", "east", "south", "west"):
            p = _port(node, subs, bb, s, offset)
            d = _dist(p, toward) if toward else 0.0
            if best is None or d < best_d:
                best, best_d = p, d
        return best
    return _port(node, subs, bb, side, offset)


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def route_edge(edge: EdgeObj, snap: SceneSnapshot) -> RouteResult:
    src = snap.get(edge.source["node"])
    dst = snap.get(edge.target["node"])
    if not isinstance(src, NodeObj) or not isinstance(dst, NodeObj):
        raise OpError("DANGLING_EDGE", f"edge {edge.id!r} endpoint is not a node",
                      object_ids=[edge.id])
    mode = edge.routing.get("mode", "straight")

    # resolve ports facing each other first for 'auto'
    sb0 = src.bbox(snap.doc)
    db0 = dst.bbox(snap.doc)
    src_c = Point(sb0.cx, sb0.cy)
    dst_c = Point(db0.cx, db0.cy)
    sp = resolve_port(src, edge.source.get("side", "auto"),
                      edge.source.get("offset"), snap, toward=dst_c)
    tp = resolve_port(dst, edge.target.get("side", "auto"),
                      edge.target.get("offset"), snap, toward=src_c)

    wps = [Point(w[0], w[1]) for w in edge.routing.get("waypoints", [])]
    warns: list[str] = []
    if mode == "manual" or mode == "straight" or not wps:
        if mode == "straight" and wps:
            pts = [sp] + wps + [tp]
        elif mode == "manual":
            pts = [sp] + wps + [tp]
        else:
            pts = [sp, tp]
    elif mode == "orthogonal":
        pts = _orthogonal(sp, tp, src.bbox(snap.doc), dst.bbox(snap.doc), warns)
    else:
        pts = [sp, tp]
        warns.append(f"unknown routing mode {mode!r}; fell back to straight")
    return RouteResult(points=pts, warnings=warns)


def _orthogonal(sp: Point, tp: Point, sb: Box, tb: Box,
                warns: list[str]) -> list[Point]:
    """Single-bend or three-segment orthogonal route.

    Picks the channel (horizontal-then-vertical vs vertical-then-horizontal)
    that doesn't pierce either endpoint box; else falls back.
    """
    # candidate 1: horizontal first (bend at (tp.x, sp.y))
    # candidate 2: vertical first (bend at (sp.x, tp.y))
    def pierces(p: Point) -> bool:
        return (sb.inflate(-1).contains_point(p) or tb.inflate(-1).contains_point(p))

    c1 = Point(tp.x, sp.y)
    c2 = Point(sp.x, tp.y)
    if abs(sp.x - tp.x) < 0.5 or abs(sp.y - tp.y) < 0.5:
        return [sp, tp]  # already aligned
    if not pierces(c1):
        return [sp, c1, tp]
    if not pierces(c2):
        return [sp, c2, tp]
    warns.append("orthogonal channel blocked by endpoint boxes; routed via midpoint channel")
    mx = (sp.x + tp.x) / 2
    return [sp, Point(mx, sp.y), Point(mx, tp.y), tp]


def polyline_to_ipe(points: list[Point], page_h: float) -> str:
    """API-space polyline -> Ipe path body."""
    if not points:
        return ""
    first = to_ipe(points[0], page_h)
    out = [f"{fmt(first.x)} {fmt(first.y)} m"]
    for p in points[1:]:
        ip = to_ipe(p, page_h)
        out.append(f"{fmt(ip.x)} {fmt(ip.y)} l")
    return "\n".join(out)


def reroute_stale_edges(doc, snap: SceneSnapshot,
                        changed: set[str] | None = None) -> tuple[int, list[str]]:
    """Rewrite path bodies for edges flagged needs_route. Returns (count, warnings).

    Also reroutes any edge whose endpoint node is in `changed` even if the flag
    was not set (defensive double-check for incremental invalidation).
    """
    changed = changed or set()
    n_routed, warns = 0, []
    for eid, edge in snap.edges.items():
        dirty = edge.needs_route or (
            edge.source.get("node") in changed or edge.target.get("node") in changed)
        if not dirty or edge.path_el is None:
            continue
        rr = route_edge(edge, snap)
        edge.path_el.text = "\n" + polyline_to_ipe(rr.points, snap.page_h) + "\n"
        # bake any matrix on the path element: we just wrote absolute coords
        if "matrix" in edge.path_el.attrib:
            del edge.path_el.attrib["matrix"]
        warns.extend(f"{eid}: {w}" for w in rr.warnings)
        # clear the flag in metadata
        from .metadata import encode_meta
        meta = dict(edge.meta)
        meta.pop("needs_route", None)
        edge.el.set("custom", encode_meta(meta))
        # reposition edge label to polyline midpoint
        if edge.label_el is not None and len(rr.points) >= 2:
            mid = _polyline_midpoint(rr.points)
            ip = to_ipe(mid, snap.page_h)
            edge.label_el.set("pos", f"{fmt(ip.x)} {fmt(ip.y)}")
        n_routed += 1
    return n_routed, warns


def _polyline_midpoint(pts: list[Point]) -> Point:
    segs = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    total = sum(_dist(a, b) for a, b in segs)
    half = total / 2
    acc = 0.0
    for a, b in segs:
        d = _dist(a, b)
        if acc + d >= half and d > 0:
            t = (half - acc) / d
            return Point(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t)
        acc += d
    return pts[len(pts) // 2]
