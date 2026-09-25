"""Edge routing: resolve semantic endpoints to outline ports, then route.

Supported modes (spec-limited, honest scope):
- straight: source port -> [waypoints] -> target port
- orthogonal: axis-aligned via a single bend channel, avoids crossing endpoint
  node boxes when a simple channel exists; falls back to straight with a
  warning when no clean channel exists.
- auto: heuristic choice — straight when endpoints are roughly row- or
  column-aligned, otherwise orthogonal. Stored as "auto" in edge metadata so
  later reroutes re-evaluate.
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
from .snapshot import EdgeObj, NodeObj, SceneSnapshot, path_subpaths

GAP = 8.0  # bp clearance from node outline for orthogonal channels

# hop-over arcs at edge crossings
HOP_R = 3.5          # hop radius (bp)
HOP_CLEAR_BP = 8.0   # no hop within this arc-length distance of an endpoint
HOP_MIN_SIN = 0.25   # skip near-parallel crossings (< ~15 deg)
HOP_MAX_PER_EDGE = 8


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
    if mode == "auto":
        # same row or column -> straight; diagonal/backward -> orthogonal
        aligned = abs(src_c.y - dst_c.y) < 15 or abs(src_c.x - dst_c.x) < 15
        mode = "straight" if aligned else "orthogonal"
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

    After routing, a global crossing pass bakes hop-over arcs into the
    topmost edge at each proper interior crossing. Edge metadata keeps the
    canonical (hop-free) polyline in ``polyline`` and the hop count in
    ``hops`` so later passes re-derive hops deterministically and stale hops
    are removed automatically.
    """
    from .metadata import encode_meta
    changed = changed or set()
    warns: list[str] = []
    canonical: dict[str, list[Point]] = {}
    dirty_ids: set[str] = set()

    for eid, edge in snap.edges.items():
        if edge.path_el is None:
            continue
        dirty = edge.needs_route or (
            edge.source.get("node") in changed or edge.target.get("node") in changed)
        if dirty:
            rr = route_edge(edge, snap)
            canonical[eid] = rr.points
            dirty_ids.add(eid)
            warns.extend(f"{eid}: {w}" for w in rr.warnings)
        else:
            mp = edge.meta.get("polyline")
            if mp:
                canonical[eid] = [Point(float(p[0]), float(p[1])) for p in mp]
            else:
                canonical[eid] = _flatten_polyline(edge.path_el, snap.page_h)

    hop_map = _detect_hops(snap, canonical)

    # obstacle boxes for label placement: node bodies, standalone texts, and
    # labels of edges that stay clean this pass (they are never repositioned).
    obstacles = _label_obstacles(snap)

    n_routed = 0
    for eid, edge in snap.edges.items():
        if edge.path_el is None or eid not in canonical:
            continue
        hops = hop_map.get(eid, [])
        hop_pts = [[round(c.x, 2), round(c.y, 2)] for _, _, c in hops]
        meta = dict(edge.meta)
        if eid in dirty_ids:
            pts = _insert_hops(canonical[eid], hops)
            edge.path_el.text = "\n" + polyline_to_ipe(pts, snap.page_h) + "\n"
            # we wrote absolute coords; any stale matrix must go
            if "matrix" in edge.path_el.attrib:
                del edge.path_el.attrib["matrix"]
            meta["polyline"] = [[round(p.x, 4), round(p.y, 4)] for p in canonical[eid]]
            _set_hop_meta(meta, hop_pts)
            meta.pop("needs_route", None)
            if edge.label_el is not None and len(canonical[eid]) >= 2:
                pos, fp_box = _best_label_pos(canonical[eid], edge, obstacles)
                ip = to_ipe(pos, snap.page_h)
                edge.label_el.set("pos", f"{fmt(ip.x)} {fmt(ip.y)}")
                obstacles.append(fp_box)
            edge.el.set("custom", encode_meta(meta))
            n_routed += 1
        else:
            # clean edge: preserve any human edits — only rewrite the path
            # when the hop set actually changed; otherwise leave the body
            # alone and just refresh the canonical polyline bookkeeping.
            hop_changed = meta.get("hop_pts", []) != hop_pts
            if hop_changed:
                pts = _insert_hops(canonical[eid], hops)
                edge.path_el.text = ("\n" + polyline_to_ipe(pts, snap.page_h)
                                     + "\n")
                if "matrix" in edge.path_el.attrib:
                    del edge.path_el.attrib["matrix"]
                _set_hop_meta(meta, hop_pts)
                edge.el.set("custom", encode_meta(meta))
            elif "polyline" not in meta:
                # capture current (possibly human-edited) geometry once
                meta["polyline"] = [[round(p.x, 4), round(p.y, 4)]
                                    for p in canonical[eid]]
                edge.el.set("custom", encode_meta(meta))
    return n_routed, warns


def _set_hop_meta(meta: dict, hop_pts: list) -> None:
    if hop_pts:
        meta["hops"] = len(hop_pts)
        meta["hop_pts"] = hop_pts
    else:
        meta.pop("hops", None)
        meta.pop("hop_pts", None)


def _flatten_polyline(el, page_h: float) -> list[Point]:
    """Best-effort canonical polyline from a baked path element (API space)."""
    pts: list[Point] = []
    for s in path_subpaths(el, page_h):
        pts.extend(Point(x, y) for x, y in s.pts)
    return pts


def _detect_hops(snap: SceneSnapshot,
                 canonical: dict[str, list[Point]]) -> dict[str, list[tuple]]:
    """Find interior crossings between managed edges.

    Returns {edge_id: [(seg_index, t, Point), ...]} for the edge that hops
    (the one drawn on top = later in page child order). Edges sharing an
    endpoint node never hop over each other (they legitimately meet).
    """
    from collections import defaultdict
    edges = [(eid, e) for eid, e in snap.edges.items()
             if e.path_el is not None and len(canonical.get(eid, [])) >= 2]
    order = {id(el): idx for idx, el in enumerate(snap.doc.page.iterchildren())}
    hops: dict[str, list[tuple]] = defaultdict(list)
    for i in range(len(edges)):
        for j in range(i + 1, len(edges)):
            id_a, ea = edges[i]
            id_b, eb = edges[j]
            shared = {ea.source.get("node"), ea.target.get("node")} & \
                     {eb.source.get("node"), eb.target.get("node")}
            if shared:
                continue
            pa, pb = canonical[id_a], canonical[id_b]
            hits_a, hits_b = [], []
            for si in range(len(pa) - 1):
                for sj in range(len(pb) - 1):
                    r = geo.seg_seg_intersection(
                        (pa[si].x, pa[si].y), (pa[si + 1].x, pa[si + 1].y),
                        (pb[sj].x, pb[sj].y), (pb[sj + 1].x, pb[sj + 1].y))
                    if r is None or r[2] < HOP_MIN_SIN:
                        continue
                    cx = pa[si].x + (pa[si + 1].x - pa[si].x) * r[0]
                    cy = pa[si].y + (pa[si + 1].y - pa[si].y) * r[0]
                    hits_a.append((si, r[0], Point(cx, cy)))
                    hits_b.append((sj, r[1], Point(cx, cy)))
            if not hits_a:
                continue
            # the edge drawn on top (later in child order) jumps over
            if order.get(id(ea.el), 0) > order.get(id(eb.el), 0):
                hops[id_a].extend(hits_a)
            else:
                hops[id_b].extend(hits_b)
    # clearance from endpoints + per-edge cap
    out: dict[str, list[tuple]] = {}
    for eid, hits in hops.items():
        poly = canonical[eid]
        cum = [0.0]
        for k in range(len(poly) - 1):
            cum.append(cum[-1] + _dist(poly[k], poly[k + 1]))
        total = cum[-1]
        kept = []
        for si, t, c in hits:
            d0 = cum[si] + t * _dist(poly[si], poly[si + 1])
            if d0 >= HOP_CLEAR_BP and total - d0 >= HOP_CLEAR_BP:
                kept.append((si, t, c))
        out[eid] = kept[:HOP_MAX_PER_EDGE]
    return out


def _insert_hops(pts: list[Point], hops: list[tuple]) -> list[Point]:
    """Insert a semicircular hop (polyline-approximated) at each crossing."""
    if not hops or len(pts) < 2:
        return pts
    by_seg: dict[int, list[tuple]] = {}
    for h in hops:
        by_seg.setdefault(h[0], []).append(h)
    out: list[Point] = []
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        out.append(a)
        seg_hops = sorted(by_seg.get(i, []), key=lambda h: h[1])
        ux, uy = b.x - a.x, b.y - a.y
        length = math.hypot(ux, uy)
        if length >= 2 * HOP_R + 2:
            ux, uy = ux / length, uy / length
            nx, ny = -uy, ux  # consistent side (left of travel)
            for _, t, c in seg_hops:
                out.append(Point(c.x - ux * HOP_R, c.y - uy * HOP_R))
                for k in range(1, 10):
                    th = math.pi * k / 10
                    out.append(Point(
                        c.x - HOP_R * math.cos(th) * ux + HOP_R * math.sin(th) * nx,
                        c.y - HOP_R * math.cos(th) * uy + HOP_R * math.sin(th) * ny))
                out.append(Point(c.x + ux * HOP_R, c.y + uy * HOP_R))
    out.append(pts[-1])
    return out


def _polyline_midpoint(pts: list[Point]) -> Point:
    return _polyline_point_at(pts, 0.5)


def _polyline_point_at(pts: list[Point], frac: float) -> Point:
    """Point at arc-length fraction ``frac`` (0..1) along the polyline."""
    segs = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    total = sum(_dist(a, b) for a, b in segs)
    if total <= 0:
        return pts[0]
    want = total * frac
    acc = 0.0
    for a, b in segs:
        d = _dist(a, b)
        if acc + d >= want and d > 0:
            t = (want - acc) / d
            return Point(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t)
        acc += d
    return pts[-1]


# arc-length fractions probed for edge-label placement, best-first
LABEL_POS_CANDIDATES = (0.5, 0.38, 0.62, 0.26, 0.74)


def _label_dims(el) -> tuple[float, float, float]:
    """(width, height, depth) of an edge label element, estimated if the
    LaTeX pass has not measured it yet."""
    w = float(el.get("width") or 36.0)
    h = float(el.get("height") or 6.0)
    d = float(el.get("depth") or 1.0)
    return w, h, d


def _label_footprint(p: Point, w: float, h: float, d: float):
    """AABB (x1,y1,x2,y2) of a label anchored at p with halign=center,
    valign=bottom (API coords)."""
    return (p.x - w / 2, p.y - h - d, p.x + w / 2, p.y)


def _aabb_hit(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _label_obstacles(snap: SceneSnapshot) -> list[tuple]:
    """Boxes edge labels should not overlap: node bodies, standalone texts,
    and current label boxes of clean (non-rerouted) edges."""
    boxes: list[tuple] = []
    for obj in snap.objects.values():
        if isinstance(obj, EdgeObj):
            continue
        bb = obj.bbox(snap.doc)
        if bb is not None:
            boxes.append((bb.x, bb.y, bb.x2, bb.y2))
    for e in snap.edges.values():
        if not e.needs_route and e.label_el is not None:
            lb = e.label_box(snap.doc)
            if lb is not None:
                boxes.append((lb.x, lb.y, lb.x2, lb.y2))
    return boxes


def _best_label_pos(pts: list[Point], edge: EdgeObj,
                    obstacles: list[tuple]) -> tuple[Point, tuple]:
    """Pick the candidate point along the polyline whose label footprint
    overlaps the fewest obstacle boxes (midpoint preferred on ties)."""
    w, h, d = _label_dims(edge.label_el)
    best_pos, best_fp, best_hits = None, None, None
    for frac in LABEL_POS_CANDIDATES:
        p = _polyline_point_at(pts, frac)
        fp = _label_footprint(p, w, h, d)
        hits = sum(1 for ob in obstacles if _aabb_hit(fp, ob))
        if best_hits is None or hits < best_hits:
            best_pos, best_fp, best_hits = p, fp, hits
            if hits == 0:
                break
    return best_pos, best_fp
