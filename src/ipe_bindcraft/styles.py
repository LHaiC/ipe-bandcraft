"""Built-in style presets (SPEC 4.2, 8.1).

`paper-default` parameters are project design defaults, not any conference's
official standard. Two semantic palettes only: `paper-muted` and
`paper-monochrome`. Styles compile into an embedded `ibc-*` stylesheet so a
document is self-contained — no external .isy path is ever required.
"""

from __future__ import annotations

from .coordinates import fmt

# ---- parameters (SPEC 4.2 design defaults) ----------------------------------

STYLE_DEFAULTS = {
    "paper-default": {
        "label_bp": 8.0,
        "secondary_bp": 7.0,
        "facet_bp": 9.0,
        "min_font_bp": 7.0,
        "pen_main_bp": 0.6,
        "pen_thin_bp": 0.45,
        "arrow_len_bp": 3.2,
        "pad_h_bp": 5.0,
        "pad_v_bp": 4.0,
        "clearance_bp": 3.0,
        "margin_bp": 6.0,
        "palette": "paper-muted",
        "corner_radius_bp": 3.0,
    }
}

PALETTES = {
    "paper-muted": {
        # restrained, print-friendly fills; all distinguishable in grayscale too
        "stroke": "0.15 0.15 0.18",
        "edge": "0.30 0.30 0.34",
        "fill_compute": "0.87 0.91 0.95",
        "fill_memory": "0.96 0.90 0.80",
        "fill_data": "0.88 0.93 0.87",
        "fill_control": "0.93 0.88 0.94",
        "fill_io": "0.95 0.95 0.90",
        "accent": "0.55 0.22 0.22",
        "warn": "0.75 0.45 0.15",
    },
    "paper-vivid": {
        # more saturated role fills, still distinguishable in grayscale
        "stroke": "0.12 0.12 0.16",
        "edge": "0.25 0.25 0.30",
        "fill_compute": "0.62 0.78 0.90",
        "fill_memory": "0.96 0.76 0.45",
        "fill_data": "0.62 0.83 0.62",
        "fill_control": "0.82 0.66 0.86",
        "fill_io": "0.90 0.85 0.55",
        "accent": "0.60 0.15 0.15",
        "warn": "0.85 0.40 0.10",
    },
    "paper-ocean": {
        # cool blue/teal family
        "stroke": "0.10 0.14 0.20",
        "edge": "0.22 0.30 0.38",
        "fill_compute": "0.72 0.85 0.93",
        "fill_memory": "0.80 0.90 0.85",
        "fill_data": "0.86 0.93 0.93",
        "fill_control": "0.65 0.78 0.88",
        "fill_io": "0.88 0.92 0.80",
        "accent": "0.20 0.45 0.55",
        "warn": "0.80 0.50 0.20",
    },
    "paper-monochrome": {
        "stroke": "0 0 0",
        "edge": "0.25 0.25 0.25",
        "fill_compute": "0.92 0.92 0.92",
        "fill_memory": "0.85 0.85 0.85",
        "fill_data": "0.97 0.97 0.97",
        "fill_control": "0.80 0.80 0.80",
        "fill_io": "0.95 0.95 0.95",
        "accent": "0.35 0.35 0.35",
        "warn": "0.55 0.55 0.55",
    },
}

# role -> fill token
ROLE_FILL = {
    "compute": "fill_compute",
    "memory": "fill_memory",
    "data": "fill_data",
    "control": "fill_control",
    "io": "fill_io",
}

TEX_PROFILES = {
    # engine must be a local pdftex for the default profile; xetex/luatex are
    # capability-gated and only enabled after an environment probe.
    "paper-serif": {
        "engine": "pdftex",
        "preamble": "",
        "description": "pdfTeX, Computer-Modern-like default serif; English+math only",
        "cjk": False,
    },
}

def list_presets(kind: str) -> list[dict]:
    out = []
    if kind == "style":
        for sid, p in STYLE_DEFAULTS.items():
            out.append({
                "id": sid,
                "description": "built-in academic style defaults (project defaults, not a journal standard)",
                "params": p,
            })
    elif kind == "palette":
        for pid, pal in PALETTES.items():
            out.append({"id": pid, "description": "semantic color palette", "params": pal})
    elif kind == "tex":
        for tid, t in TEX_PROFILES.items():
            out.append({"id": tid, "description": t["description"], "params": t})
    elif kind == "template":
        from .templates import TEMPLATES
        for tid in sorted(TEMPLATES):
            out.append({"id": tid, "description": "starter template (example layout, not an official standard)"})
    elif kind == "icon":
        from .icons import list_icons
        out.extend(list_icons())
    return out


def role_fill_color(role: str | None, palette: str) -> str:
    pal = PALETTES.get(palette, PALETTES["paper-muted"])
    token = ROLE_FILL.get(role or "", "fill_data")
    return pal[token]


def stylesheet_xml(style_id: str, page_w: float, page_h: float,
                   palette: str | None = None) -> str:
    """Embedded stylesheet for a new document."""
    p = STYLE_DEFAULTS.get(style_id, STYLE_DEFAULTS["paper-default"])
    pal_name = palette or p["palette"]
    pal = PALETTES.get(pal_name, PALETTES["paper-muted"])
    colors = "\n".join(
        f'<color name="ibc-{k}" value="{v}"/>' for k, v in pal.items()
    )
    return f"""<ipestyle name="ibc-{style_id}">
<layout paper="{fmt(page_w)} {fmt(page_h)}" origin="0 0" frame="{fmt(page_w)} {fmt(page_h)}" crop="yes"/>
{colors}
<pen name="ibc-main" value="{fmt(p['pen_main_bp'])}"/>
<pen name="ibc-thin" value="{fmt(p['pen_thin_bp'])}"/>
<arrowsize name="ibc-arrow" value="{fmt(p['arrow_len_bp'])}"/>
</ipestyle>"""
