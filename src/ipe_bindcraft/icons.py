"""Curated icons drawn as native Ipe path strokes.

Each icon is authored in a normalized 0..100 design box (y-up for human
readability) and `render_icon` flips to API convention (y-down, origin
top-left). Compiled into a <group> of <path> children — no external
assets, no SVG import. Arcs and circles are approximated by polylines.

Stroke = dict(points=[(x, y), ...], closed=bool, fill=bool).
"""

from __future__ import annotations

import math
from typing import Callable

Point2 = tuple[float, float]
Stroke = dict  # {"points": list[Point2], "closed": bool, "fill": bool}


def _arc(cx: float, cy: float, rx: float, ry: float,
         a0: float, a1: float, n: int = 20) -> list[Point2]:
    """Polyline approximation of an elliptical arc (degrees)."""
    return [
        (cx + rx * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
         cy + ry * math.sin(math.radians(a0 + (a1 - a0) * i / n)))
        for i in range(n + 1)
    ]


def _circle(cx: float, cy: float, r: float, n: int = 28) -> list[Point2]:
    return _arc(cx, cy, r, r, 0, 360, n)


def _line(*pts: Point2) -> Stroke:
    return {"points": list(pts), "closed": False, "fill": False}


def _poly(*pts: Point2, fill: bool = False) -> Stroke:
    return {"points": list(pts), "closed": True, "fill": fill}


def _stroke(pts: list[Point2], *, closed: bool = False, fill: bool = False) -> Stroke:
    return {"points": pts, "closed": closed, "fill": fill}


# ---------------------------------------------------------------------------
# icon glyphs (0..100 box, y-up)
# ---------------------------------------------------------------------------

def _database() -> list[Stroke]:
    ry = 14
    top = _arc(50, 86, 32, ry, 0, 360)
    return [
        _stroke(top, closed=True),
        _line((18, 86), (18, 18)),
        _line((82, 86), (82, 18)),
        _stroke(_arc(50, 18, 32, ry, 180, 360)),
        _stroke(_arc(50, 52, 32, ry, 180, 360)),
    ]


def _cloud() -> list[Stroke]:
    # flat base + three tangent-ish bumps forming the silhouette
    return [
        _line((26, 34), (74, 34)),
        _line((23, 44), (26, 34)),
        _line((77, 44), (74, 34)),
        _stroke(_arc(34, 44, 11, 11, 180, 90)),   # left bump
        _stroke(_arc(50, 50, 16, 16, 160, 20)),   # main dome
        _stroke(_arc(66, 44, 11, 11, 90, 0)),     # right bump
    ]


def _server() -> list[Stroke]:
    out = []
    for y in (10, 40, 70):
        out.append(_poly((20, y), (80, y), (80, y + 22), (20, y + 22)))
        out.append(_stroke(_circle(28, y + 11, 2.5, 10), closed=True, fill=True))
        out.append(_line((60, y + 11), (74, y + 11)))
    return out


def _gear() -> list[Stroke]:
    teeth = 8
    pts: list[Point2] = []
    for k in range(teeth):
        a0 = k * 360 / teeth
        a1 = a0 + 360 / teeth * 0.28
        a2 = a0 + 360 / teeth * 0.5
        a3 = a0 + 360 / teeth * 0.78
        for a, r in ((a0, 30), (a0, 40), (a1, 40), (a1, 30), (a2, 30)):
            pts.append((50 + r * math.cos(math.radians(a)),
                        50 + r * math.sin(math.radians(a))))
        # valley to next tooth start angle
        for a, r in ((a3, 30),):
            pts.append((50 + r * math.cos(math.radians(a)),
                        50 + r * math.sin(math.radians(a))))
    return [_stroke(pts, closed=True),
            _stroke(_circle(50, 50, 12), closed=True)]


def _cpu() -> list[Stroke]:
    out = [_poly((25, 25), (75, 25), (75, 75), (25, 75)),
           _poly((36, 36), (64, 36), (64, 64), (36, 64))]
    for x in (35, 50, 65):  # pins on all four sides
        out += [_line((x, 75), (x, 88)), _line((x, 12), (x, 25)),
                _line((25, x), (12, x)), _line((75, x), (88, x))]
    return out


def _document() -> list[Stroke]:
    return [
        _stroke([(25, 8), (25, 92), (62, 92), (75, 79), (75, 8)], closed=True),
        _stroke([(62, 92), (62, 79), (75, 79)]),
        _line((34, 66), (66, 66)),
        _line((34, 52), (66, 52)),
        _line((34, 38), (58, 38)),
    ]


def _folder() -> list[Stroke]:
    return [_stroke([(15, 18), (15, 72), (40, 72), (48, 80),
                     (85, 80), (85, 18)], closed=True),
            _line((15, 60), (85, 60))]


def _user() -> list[Stroke]:
    return [
        _stroke(_circle(50, 66, 16), closed=True),
        _stroke(_arc(50, 16, 28, 24, 15, 165) , closed=False),
    ]


def _lock() -> list[Stroke]:
    return [
        _poly((28, 10), (72, 10), (72, 48), (28, 48)),
        _stroke(_arc(50, 48, 14, 14, 0, 180)),
        _stroke(_circle(50, 30, 4, 12), closed=True, fill=True),
    ]


def _globe() -> list[Stroke]:
    return [
        _stroke(_circle(50, 50, 34), closed=True),
        _stroke(_arc(50, 50, 15, 34, 0, 360), closed=True),
        _line((16, 50), (84, 50)),
        _stroke(_arc(50, 50, 34, 12, 200, 340)),
        _stroke(_arc(50, 50, 34, 12, 20, 160)),
    ]


def _lightning() -> list[Stroke]:
    return [_stroke([(58, 96), (30, 46), (48, 46), (40, 6),
                     (72, 52), (52, 52)], closed=True, fill=True)]


def _clock() -> list[Stroke]:
    return [
        _stroke(_circle(50, 50, 34), closed=True),
        _line((50, 50), (50, 74)),
        _line((50, 50), (66, 44)),
        _stroke(_circle(50, 50, 2.5, 10), closed=True, fill=True),
    ]


def _search() -> list[Stroke]:
    return [
        _stroke(_circle(44, 56, 26), closed=True),
        _line((63, 37), (84, 16)),
    ]


def _refresh() -> list[Stroke]:
    # arc covers 40..320 deg (gap on the right); arrowhead sits at the arc
    # start point (73,69.3) pointing along the clockwise tangent (down-right)
    return [
        _stroke(_arc(50, 50, 30, 30, 40, 320)),
        _stroke([(82, 58.5), (76.5, 76.1), (65.7, 67.1)], closed=True,
                fill=True),
    ]


def _warning() -> list[Stroke]:
    # taller triangle; "!" sits higher and clear of the base edge
    return [
        _stroke([(50, 94), (90, 14), (10, 14)], closed=True),
        _line((50, 70), (50, 46)),
        _stroke(_circle(50, 33, 3.8, 10), closed=True, fill=True),
    ]


ICONS: dict[str, Callable[[], list[Stroke]]] = {
    "database": _database,
    "cloud": _cloud,
    "server": _server,
    "gear": _gear,
    "cpu": _cpu,
    "document": _document,
    "folder": _folder,
    "user": _user,
    "lock": _lock,
    "globe": _globe,
    "lightning": _lightning,
    "clock": _clock,
    "search": _search,
    "refresh": _refresh,
    "warning": _warning,
}


def list_icons() -> list[dict]:
    return [{"id": name, "strokes": len(ICONS[name]())} for name in sorted(ICONS)]


def render_icon(name: str) -> list[Stroke]:
    """Strokes in API convention (y-down); authored y-up, flipped here."""
    fn = ICONS.get(name)
    if fn is None:
        from .errors import IbcError
        raise IbcError("VALIDATION", f"unknown icon {name!r}",
                       details={"known": sorted(ICONS)})
    return [{**st, "points": [(x, 100.0 - y) for x, y in st["points"]]}
            for st in fn()]
