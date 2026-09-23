"""Geometry for ipe-bindcraft: path parsing, outline flattening, ports.

All scene computation happens in API space (top-left origin, y down, bp).
`parse_path` reads raw Ipe path text and applies a matrix; callers pass the
page flip ``(1,0,0,-1,0,H)`` combined with the object matrix so results land
in API space. We only *emit* m/l/c/e/h operators.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .coordinates import Box, Point, TOL_BP, from_ipe, to_ipe

# ---------------------------------------------------------------------------
# Affine matrices (Ipe writes a b c d s t, column-major):
#   x' = a*x + c*y + s ;  y' = b*x + d*y + t
# ---------------------------------------------------------------------------

Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mat_apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, s, t = m
    return (a * x + c * y + s, b * x + d * y + t)


def mat_mul(m1: Matrix, m2: Matrix) -> Matrix:
    """m1 ∘ m2 (apply m2 first)."""
    a1, b1, c1, d1, s1, t1 = m1
    a2, b2, c2, d2, s2, t2 = m2
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * s2 + c1 * t2 + s1,
        b1 * s2 + d1 * t2 + t1,
    )


def mat_translate(dx: float, dy: float) -> Matrix:
    return (1.0, 0.0, 0.0, 1.0, dx, dy)


def mat_is_identity(m: Matrix, tol: float = 1e-6) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(m, IDENTITY))


def mat_is_translation(m: Matrix, tol: float = 1e-6) -> bool:
    a, b, c, d, s, t = m
    return abs(a - 1) <= tol and abs(d - 1) <= tol and abs(b) <= tol and abs(c) <= tol


def mat_translation(m: Matrix) -> tuple[float, float]:
    return (m[4], m[5])


def parse_matrix(text: str | None) -> Matrix:
    if not text:
        return IDENTITY
    parts = [float(v) for v in text.split()]
    if len(parts) != 6:
        raise ValueError(f"bad matrix {text!r}")
    return tuple(parts)  # type: ignore[return-value]


def format_matrix(m: Matrix) -> str:
    from .coordinates import fmt

    return " ".join(fmt(v) for v in m)


# ---------------------------------------------------------------------------
# Path model
# ---------------------------------------------------------------------------

@dataclass
class Subpath:
    """A flattened subpath: ordered points, closed flag."""

    pts: list[tuple[float, float]] = field(default_factory=list)
    closed: bool = False
    kind: str = "poly"  # "poly" | "ellipse" — ellipse is a unit-circle image

    def bbox(self) -> tuple[float, float, float, float] | None:
        if not self.pts:
            return None
        xs = [p[0] for p in self.pts]
        ys = [p[1] for p in self.pts]
        return (min(xs), min(ys), max(xs), max(ys))


_TOKEN = re.compile(r"[a-zA-Z*]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _tokenize(body: str) -> list[str]:
    return _TOKEN.findall(body)


def parse_path(body: str, matrix: Matrix = IDENTITY) -> list[Subpath]:
    """Parse Ipe path-operator text into flattened subpaths.

    Ipe path syntax is *postfix*: coordinate operands come first, then the
    operator letter (``x y m``, ``x y l``, ``<6-matrix> e``,
    ``<6-matrix> x y a``, ``p1 ... pn c``, ``h``). Supports m, l, c (cubic
    B-spline), e (ellipse), a (arc), u (closed uniform B-spline), h (close),
    C (cardinal), L (spiro — uses its precomputed Bézier approximation
    following the '*' marker).
    """
    toks = _tokenize(body)
    subs: list[Subpath] = []
    cur: Subpath | None = None
    pos = (0.0, 0.0)
    nums: list[float] = []  # pending operand stack
    i = 0
    n = len(toks)

    def take_pts(k: int) -> list[tuple[float, float]]:
        """Pop the last k point-pairs off the operand stack (oldest first)."""
        need = 2 * k
        if len(nums) < need:
            raise ValueError("path operator missing operands")
        vals = nums[-need:]
        del nums[-need:]
        return [(vals[j], vals[j + 1]) for j in range(0, need, 2)]

    def take_all_pts() -> list[tuple[float, float]]:
        if len(nums) % 2:
            raise ValueError("odd operand count for spline points")
        vals, nums[:] = nums[:], []
        return [(vals[j], vals[j + 1]) for j in range(0, len(vals), 2)]

    def take_matrix() -> Matrix:
        if len(nums) < 6:
            raise ValueError("path operator missing matrix operands")
        vals = nums[-6:]
        del nums[-6:]
        return (vals[0], vals[1], vals[2], vals[3], vals[4], vals[5])

    while i < n:
        tok = toks[i]
        i += 1
        if not tok.isalpha() and tok != "*":
            nums.append(float(tok))
            continue
        if tok == "*":  # spiro separator: Bézier approximation follows 'L'
            continue
        op = tok
        if op == "m":
            p = take_pts(1)[0]
            cur = Subpath(pts=[p])
            subs.append(cur)
            pos = p
        elif op == "l":
            p = take_pts(1)[0]
            if cur is None:
                cur = Subpath(pts=[pos])
                subs.append(cur)
            cur.pts.append(p)
            pos = p
        elif op in ("c", "q", "s"):
            # cubic B-spline: all pending points are control points
            ctrl = take_all_pts()
            if cur is None:
                cur = Subpath(pts=[pos])
                subs.append(cur)
            segs = _bspline_to_beziers(pos, ctrl, closed=False)
            for seg in segs:
                cur.pts.extend(_flatten_cubic(*seg))
            if segs:
                pos = segs[-1][3]
        elif op == "e":
            m = take_matrix()
            pts = _flatten_ellipse(m)
            cur = Subpath(pts=pts, closed=True, kind="ellipse")
            subs.append(cur)
            pos = pts[-1]
        elif op == "a":
            # postfix order is "<6 matrix numbers> <x y> a": the endpoint is
            # on TOP of the operand stack, so pop it before the matrix.
            p = take_pts(1)[0]
            m = take_matrix()
            if cur is None:
                cur = Subpath(pts=[pos])
                subs.append(cur)
            cur.pts.extend(_flatten_arc(m, pos, p))
            pos = p
        elif op == "u":
            ctrl = take_all_pts()
            cur = Subpath()
            segs = _bspline_to_beziers(ctrl[0] if ctrl else pos, ctrl, closed=True)
            if ctrl:
                cur.pts.append(ctrl[0])
            for seg in segs:
                cur.pts.extend(_flatten_cubic(*seg))
            cur.closed = True
            subs.append(cur)
            if ctrl:
                pos = ctrl[0]
        elif op == "C":
            # cardinal spline: points then tension
            if not nums:
                raise ValueError("cardinal spline missing operands")
            vals, nums[:] = nums[:], []
            pts = [(vals[j], vals[j + 1]) for j in range(0, len(vals) - 2, 2)]
            if cur is None:
                cur = Subpath(pts=[pos])
                subs.append(cur)
            cur.pts.extend(pts[1:] if len(pts) > 1 else pts)
            if pts:
                pos = pts[-1]
        elif op == "L":
            # spiro: defining points precede 'L'; a '*' + Bézier approximation
            # may follow. Pass through the defining polyline.
            pts = take_all_pts()
            if i < n and toks[i] == "*":
                i += 1
                approx: list[float] = []
                while i < n and not toks[i].isalpha():
                    approx.append(float(toks[i]))
                    i += 1
                ap = [(approx[j], approx[j + 1])
                      for j in range(0, len(approx) - 1, 2)]
                if len(ap) >= 4:
                    pts = ap[::3] + [ap[-1]]
            if cur is None:
                cur = Subpath(pts=[pos])
                subs.append(cur)
            cur.pts.extend(pts)
            if pts:
                pos = pts[-1]
        elif op == "h":
            if cur is not None:
                cur.closed = True
                cur = None
        else:
            raise ValueError(f"unsupported path operator {op!r}")

    if nums:
        raise ValueError(f"path has {len(nums)} dangling operands")
    if matrix != IDENTITY:
        for s in subs:
            s.pts = [mat_apply(matrix, *p) for p in s.pts]
    return subs


def parse_matrix_from_tokens(toks: list[str], i: int) -> Matrix:
    return tuple(float(toks[i + k]) for k in range(6))  # type: ignore[return-value]


def _flatten_cubic(p0, p1, p2, p3, n: int = 16) -> list[tuple[float, float]]:
    out = []
    for k in range(1, n + 1):
        t = k / n
        mt = 1 - t
        x = mt**3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t**3 * p3[0]
        y = mt**3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t**3 * p3[1]
        out.append((x, y))
    return out


def _bspline_to_beziers(p0, ctrl, closed: bool) -> list[tuple]:
    """Convert a uniform cubic B-spline (current pos + control points) to Béziers."""
    pts = [p0] + list(ctrl)
    if len(pts) < 4:
        # not enough points for a cubic segment; emit straight lines
        return [(pts[j], pts[j], pts[j + 1], pts[j + 1]) for j in range(len(pts) - 1)]
    out = []
    for j in range(len(pts) - 3):
        b0 = pts[j]
        b3 = pts[j + 3]
        c1 = (pts[j][0] * (1 / 3) + pts[j + 1][0] * (2 / 3), pts[j][1] / 3 + pts[j + 1][1] * 2 / 3)
        c2 = (pts[j + 1][0] * (2 / 3) + pts[j + 2][0] / 3, pts[j + 1][1] * 2 / 3 + pts[j + 2][1] / 3)
        out.append((b0, c1, c2, b3))
    return out


def _flatten_ellipse(m: Matrix, n: int = 48) -> list[tuple[float, float]]:
    """Ellipse = image of unit circle under matrix m."""
    pts = []
    for k in range(n):
        t = 2 * math.pi * k / n
        pts.append(mat_apply(m, math.cos(t), math.sin(t)))
    pts.append(pts[0])
    return pts


def _flatten_arc(m: Matrix, start, end, n: int = 24) -> list[tuple[float, float]]:
    """Elliptic arc on ellipse(m) from current pos to end point.

    Inverse-map endpoints to the unit circle, then sweep the arc the short way.
    """
    a, b, c, d, s, t = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        return [end]
    ia, ib, ic, idd = d / det, -b / det, -c / det, a / det

    def inv(p):
        x, y = p[0] - s, p[1] - t
        return (ia * x + ic * y, ib * x + idd * y)

    u0, u1 = inv(start), inv(end)
    a0, a1 = math.atan2(u0[1], u0[0]), math.atan2(u1[1], u1[0])
    da = a1 - a0
    while da > math.pi:
        da -= 2 * math.pi
    while da < -math.pi:
        da += 2 * math.pi
    pts = []
    for k in range(1, n + 1):
        ang = a0 + da * k / n
        pts.append(mat_apply(m, math.cos(ang), math.sin(ang)))
    pts[-1] = end
    return pts


def subpaths_bbox(subs: list[Subpath]) -> tuple[float, float, float, float] | None:
    boxes = [s.bbox() for s in subs if s.pts]
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


# ---------------------------------------------------------------------------
# Ports: point on the actual outline
# ---------------------------------------------------------------------------

SIDES = ("north", "east", "south", "west")


def _side_anchor(bbox: tuple[float, float, float, float], side: str, offset: float) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox
    if side == "north":
        return (x1 + offset * (x2 - x1), y1)
    if side == "south":
        return (x1 + offset * (x2 - x1), y2)
    if side == "west":
        return (x1, y1 + offset * (y2 - y1))
    return (x2, y1 + offset * (y2 - y1))  # east


def _seg_intersect_ray(o, d, a, b):
    """Intersection of ray o+t*d (t>=0) with segment a->b. Returns t or None."""
    rx, ry = d
    ax, ay = a
    bx, by = b
    sx, sy = bx - ax, by - ay
    denom = rx * sy - ry * sx
    if abs(denom) < 1e-12:
        return None
    t = ((ax - o[0]) * sy - (ay - o[1]) * sx) / denom
    u = ((ax - o[0]) * ry - (ay - o[1]) * rx) / denom
    if t >= -1e-9 and -1e-9 <= u <= 1 + 1e-9:
        return max(t, 0.0)
    return None


def flip_matrix(page_h: float) -> Matrix:
    """Ipe-space -> API-space flip for a page of height ``page_h`` bp."""
    return (1.0, 0.0, 0.0, -1.0, 0.0, page_h)


def port_point(
    subs: list[Subpath],
    bbox: tuple[float, float, float, float],
    side: str,
    offset: float | None,
    center: tuple[float, float] | None = None,
) -> tuple[float, float]:
    """Boundary point for a port on the actual outline (API coords).

    Semantics: take the anchor on the *bounding-box* side at `offset`, cast a
    ray from the shape center through it, and return the outline intersection
    nearest to the anchor. For a rect this is exactly the side point; for
    ellipse/diamond it is the real outline point (no bbox shortcut).
    """
    if side == "auto":
        side, offset = _auto_side(subs, bbox, center)
    off = 0.5 if offset is None else offset
    x1, y1, x2, y2 = bbox
    c = center or ((x1 + x2) / 2, (y1 + y2) / 2)
    anchor = _side_anchor(bbox, side, off)
    dx, dy = anchor[0] - c[0], anchor[1] - c[1]
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return anchor
    best_t = None
    for s in subs:
        pts = s.pts
        rng = range(len(pts) - 1) if not s.closed else range(len(pts))
        for j in rng:
            a = pts[j]
            b = pts[(j + 1) % len(pts)]
            tt = _seg_intersect_ray(c, (dx, dy), a, b)
            if tt is not None and (best_t is None or tt < best_t):
                best_t = tt
    if best_t is None or best_t < 1e-9:
        return anchor
    return (c[0] + dx * best_t, c[1] + dy * best_t)


def _auto_side(subs, bbox, center):
    # pick side whose outward direction best matches the dominant axis
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    return ("east" if w >= h else "south"), 0.5


def point_in_poly(pt, poly, closed=True) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi:
                inside = not inside
    return inside


def seg_intersects_box(a, b, bbox, tol: float = TOL_BP) -> bool:
    """Segment intersects an inflated box (Ipe coords bbox x1 y1 x2 y2)."""
    x1, y1, x2, y2 = bbox
    # quick reject
    if max(a[0], b[0]) < x1 - tol or min(a[0], b[0]) > x2 + tol:
        return False
    if max(a[1], b[1]) < y1 - tol or min(a[1], b[1]) > y2 + tol:
        return False
    # endpoint inside?
    for p in (a, b):
        if x1 - tol <= p[0] <= x2 + tol and y1 - tol <= p[1] <= y2 + tol:
            return True
    # edge intersections
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    for i in range(4):
        if _segs_cross(a, b, corners[i], corners[(i + 1) % 4]):
            return True
    return False


def _segs_cross(p1, p2, p3, p4) -> bool:
    def ccw(a, b, c):
        return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])

    return ccw(p1, p3, p4) != ccw(p2, p3, p4) and ccw(p1, p2, p3) != ccw(p1, p2, p4)


def polyline_length(pts) -> float:
    return sum(
        math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        for i in range(len(pts) - 1)
    )


def simplify_polyline(pts, tol: float = TOL_BP):
    """Drop zero-length segments and redundant collinear points."""
    if len(pts) <= 2:
        return list(pts)
    out = [pts[0]]
    for p in pts[1:-1]:
        prev = out[-1]
        if math.hypot(p[0] - prev[0], p[1] - prev[1]) < 1e-6:
            continue
        out.append(p)
    out.append(pts[-1])
    # remove collinear
    res = [out[0]]
    for i in range(1, len(out) - 1):
        a, b, c = res[-1], out[i], out[i + 1]
        cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if abs(cross) > tol * max(1.0, math.hypot(c[0] - a[0], c[1] - a[1])):
            res.append(b)
    res.append(out[-1])
    return res
