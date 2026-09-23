"""Coordinate contract (SPEC 4.1).

API space: origin top-left, x right, y down, all units bp (1/72 inch).
Ipe space: origin bottom-left, x right, y up.

    X_ipe = x
    Y_ipe = H - y            (H = page height in bp)
    rect lower-left = (x, H - y - height)

All finite values only; serialization caps at 4 decimals; geometry tolerance
is 0.1 bp.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

TOL_BP = 0.1
MAX_DECIMALS = 4


def check_finite(*vals: float) -> None:
    for v in vals:
        if not math.isfinite(v):
            raise ValueError(f"non-finite coordinate: {v!r}")


def fmt(v: float) -> str:
    """Serialize a coordinate with at most 4 decimals, no trailing zeros."""
    check_finite(v)
    s = f"{v:.{MAX_DECIMALS}f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def __post_init__(self):
        check_finite(self.x, self.y)


@dataclass(frozen=True)
class Box:
    """Axis-aligned box in API space (x,y = top-left, y down)."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self):
        check_finite(self.x, self.y, self.width, self.height)
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"box must have positive size: {self.width}x{self.height}")

    @property
    def cx(self) -> float:
        return self.x + self.width / 2

    @property
    def cy(self) -> float:
        return self.y + self.height / 2

    @property
    def x2(self) -> float:
        return self.x + self.width

    @property
    def y2(self) -> float:
        return self.y + self.height

    def translate(self, dx: float, dy: float) -> "Box":
        return Box(self.x + dx, self.y + dy, self.width, self.height)

    def inflate(self, d: float) -> "Box":
        return Box(self.x - d, self.y - d, self.width + 2 * d, self.height + 2 * d)

    def intersects(self, other: "Box", tol: float = TOL_BP) -> bool:
        return not (
            self.x2 <= other.x + tol
            or other.x2 <= self.x + tol
            or self.y2 <= other.y + tol
            or other.y2 <= self.y + tol
        )

    def contains_point(self, p: Point, tol: float = 0.0) -> bool:
        return (
            self.x - tol <= p.x <= self.x2 + tol
            and self.y - tol <= p.y <= self.y2 + tol
        )

    def inside_page(self, page_w: float, page_h: float, margin: float = 0.0) -> bool:
        return (
            self.x >= margin
            and self.y >= margin
            and self.x2 <= page_w - margin
            and self.y2 <= page_h - margin
        )


def to_ipe(p: Point, page_h: float) -> Point:
    """API point -> Ipe point (flip y)."""
    return Point(p.x, page_h - p.y)


def from_ipe(p: Point, page_h: float) -> Point:
    """Ipe point -> API point."""
    return Point(p.x, page_h - p.y)


def box_to_ipe_rect(b: Box, page_h: float) -> tuple[float, float, float, float]:
    """API box -> Ipe-space rect corners (x1 y1 lower-left, x2 y2 upper-right)."""
    return (b.x, page_h - b.y - b.height, b.x2, page_h - b.y)


def ipe_rect_to_box(x1: float, y1: float, x2: float, y2: float, page_h: float) -> Box:
    """Ipe-space corners -> API box."""
    return Box(x1, page_h - y2, x2 - x1, y2 - y1)
