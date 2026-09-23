import math
import pytest

from ipe_bindcraft import geometry as geo
from ipe_bindcraft.coordinates import Box, Point, from_ipe, to_ipe


def test_postfix_path_parse():
    subs = geo.parse_path("10 20 m\n30 20 l\n30 40 l\nh\n")
    assert len(subs) == 1
    assert subs[0].closed
    assert subs[0].pts == [(10, 20), (30, 20), (30, 40)]


def test_ellipse_operator():
    subs = geo.parse_path("4 0 0 2 100 100 e")
    xs = [p[0] for p in subs[0].pts]
    ys = [p[1] for p in subs[0].pts]
    assert min(xs) == pytest.approx(96) and max(xs) == pytest.approx(104)
    assert min(ys) == pytest.approx(98) and max(ys) == pytest.approx(102)


def test_arc_operator_postfix():
    # quarter arc: ellipse matrix r=10 at origin, from (10,0) to (0,10)
    subs = geo.parse_path("10 0 m\n10 0 0 10 0 0 0 10 a")
    pts = subs[0].pts
    assert pts[-1] == (0, 10)
    # arc stays on radius 10
    for x, y in pts[1:]:
        assert math.hypot(x, y) == pytest.approx(10, abs=0.1)


def test_dangling_operands_rejected():
    with pytest.raises(ValueError):
        geo.parse_path("10 20")


def test_flip_roundtrip():
    p = Point(50, 100)
    assert from_ipe(to_ipe(p, 320), 320) == p
    assert to_ipe(Point(0, 0), 320) == Point(0, 320)


def test_port_on_rect_outline():
    subs = geo.parse_path("0 0 m\n100 0 l\n100 50 l\n0 50 l\nh")
    bb = (0, 0, 100, 50)
    assert geo.port_point(subs, bb, "east", 0.5) == (100, 25)
    assert geo.port_point(subs, bb, "north", 0.25) == (25, 0)


def test_port_on_ellipse_is_on_outline_not_bbox():
    subs = geo.parse_path("40 0 0 20 100 100 e")  # rx=40 ry=20 @ (100,100)
    bb = (60, 80, 140, 120)
    # corner-ish direction: offset 0.0 on east side casts ray to (140,100)... 
    # use diagonal: south port offset 0.5 -> point on ellipse outline
    px, py = geo.port_point(subs, bb, "south", 0.5)
    assert px == pytest.approx(100) and py == pytest.approx(120)
    # northeast diagonal: offset ~0.25 on east casts a ray hitting the ellipse
    # before the bbox edge
    px, py = geo.port_point(subs, bb, "east", 0.25)
    u = (px - 100) / 40
    v = (py - 100) / 20
    assert u * u + v * v == pytest.approx(1.0, abs=0.05)


def test_mat_translation_helpers():
    m = geo.mat_translate(5, -3)
    assert geo.mat_is_translation(m)
    assert geo.mat_translation(m) == (5, -3)
    assert not geo.mat_is_translation((2, 0, 0, 2, 0, 0))
