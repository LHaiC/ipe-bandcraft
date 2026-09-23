"""Unit tests for the scientific-figure extras: auto routing, curated icons,
instantiable templates, palettes.
"""

import pytest
from pydantic import TypeAdapter, ValidationError

from ipe_bindcraft import icons, templates
from ipe_bindcraft.schemas import Operation
from ipe_bindcraft.templates import DESIGN_H, DESIGN_W

OPS = TypeAdapter(list[Operation])


def op(d):
    return OPS.validate_python([d])[0]


# -- auto routing schema ------------------------------------------------------

def test_routing_auto_mode_accepted():
    o = op({"op": "edge.create", "id": "e1",
            "source": {"node": "a"}, "target": {"node": "b"},
            "routing": {"mode": "auto"}})
    assert o.routing.mode == "auto"


def test_routing_unknown_mode_rejected():
    with pytest.raises(ValidationError):
        op({"op": "edge.create", "id": "e1",
            "source": {"node": "a"}, "target": {"node": "b"},
            "routing": {"mode": "diagonal"}})


# -- icons --------------------------------------------------------------------

def test_icon_create_schema():
    o = op({"op": "icon.create", "id": "ic", "name": "database",
            "box": {"x": 10, "y": 10, "width": 24, "height": 24}})
    assert o.name == "database"


def test_icon_registry_sane():
    assert len(icons.ICONS) >= 15
    for name in icons.ICONS:
        strokes = icons.render_icon(name)
        assert strokes, name
        for st in strokes:
            assert st["points"], name
            for x, y in st["points"]:
                assert 0 <= x <= 100 and 0 <= y <= 100, (name, x, y)


def test_unknown_icon_raises():
    from ipe_bindcraft.errors import IbcError
    with pytest.raises(IbcError) as e:
        icons.render_icon("nonexistent")
    assert e.value.code == "VALIDATION"


def test_list_icons_preset():
    from ipe_bindcraft.styles import list_presets
    names = {p["id"] for p in list_presets("icon")}
    assert {"database", "cloud", "gear"} <= names


# -- templates ----------------------------------------------------------------

def test_all_templates_validate_as_ops():
    for name in templates.TEMPLATES:
        ops = OPS.validate_python(templates.render_template(name, DESIGN_W, DESIGN_H))
        assert len(ops) > 3, name


def test_template_scaling_preserves_ratio():
    ops = templates.render_template("system_overview", DESIGN_W * 2, DESIGN_H)
    node = next(o for o in ops if o["op"] == "node.create" and o["id"] == "src")
    assert node["box"]["x"] == pytest.approx(40)
    assert node["box"]["y"] == pytest.approx(130)  # y unchanged
    assert node["box"]["width"] == pytest.approx(120)


def test_unknown_template_raises():
    from ipe_bindcraft.errors import IbcError
    with pytest.raises(IbcError):
        templates.render_template("bogus", 100, 100)


# -- palettes -----------------------------------------------------------------

def test_new_palettes_present():
    from ipe_bindcraft.styles import PALETTES, list_presets
    assert {"paper-muted", "paper-monochrome", "paper-vivid", "paper-ocean"} <= set(PALETTES)
    ids = {p["id"] for p in list_presets("palette")}
    assert "paper-vivid" in ids


def test_stylesheet_uses_chosen_palette():
    from ipe_bindcraft.styles import stylesheet_xml
    xml = stylesheet_xml("paper-default", 400, 300, palette="paper-ocean")
    assert 'name="ibc-fill_compute" value="0.72 0.85 0.93"' in xml
