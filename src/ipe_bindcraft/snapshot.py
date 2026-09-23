"""SceneSnapshot — a derived, read-only view of the current .ipe DOM.

This is a *cache* built from the authoritative XML, never a second source of
truth. It maps stable ids to native elements and exposes API-space geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lxml import etree

from .coordinates import Box, Point
from .document import OBJECT_TAGS, IpeDoc, build_index, get_custom
from .errors import IbcError
from . import geometry as geo


def _flip(page_h: float) -> geo.Matrix:
    return geo.flip_matrix(page_h)


def el_matrix(el: etree._Element) -> geo.Matrix:
    return geo.parse_matrix(el.get("matrix"))


def combined_matrix(el: etree._Element, page_h: float) -> geo.Matrix:
    """Ipe-space matrix of el including ancestor group matrices, then flip."""
    m = geo.IDENTITY
    chain: list[etree._Element] = []
    cur = el
    while cur is not None and cur.tag in OBJECT_TAGS:
        chain.append(cur)
        cur = cur.getparent()
    for anc in reversed(chain):
        m = geo.mat_mul(m, el_matrix(anc))
    return geo.mat_mul(_flip(page_h), m)


def path_subpaths(el: etree._Element, page_h: float) -> list[geo.Subpath]:
    """Flattened outline of a <path> element in API space."""
    m = combined_matrix(el, page_h)
    return geo.parse_path(el.text or "", m)


def path_bbox_api(el: etree._Element, page_h: float) -> tuple[float, float, float, float] | None:
    subs = path_subpaths(el, page_h)
    bb = geo.subpaths_bbox(subs)
    return bb


def _bbox_to_box(bb: tuple[float, float, float, float] | None) -> Box | None:
    """bbox tuple -> Box, clamping degenerate (zero-area) extents."""
    if bb is None:
        return None
    w = max(bb[2] - bb[0], 1e-3)
    h = max(bb[3] - bb[1], 1e-3)
    return Box(bb[0], bb[1], w, h)


def text_pos_api(el: etree._Element, page_h: float) -> Point:
    pos = el.get("pos") or "0 0"
    x, y = (float(v) for v in pos.split()[:2])
    m = combined_matrix(el, page_h)
    px, py = geo.mat_apply(m, x, y)
    return Point(px, py)


def text_box_api(el: etree._Element, page_h: float) -> Box | None:
    """Visible box of a text object (needs width/height/depth — set by LaTeX)."""
    w = el.get("width")
    h = el.get("height")
    d = el.get("depth")
    pos = text_pos_api(el, page_h)
    if w is None or h is None:
        return None
    w, h, d = float(w), float(h), float(d or 0)
    ha = el.get("halign", "left")
    va = el.get("valign", "baseline")
    x = pos.x - (w / 2 if ha == "center" else w if ha == "right" else 0)
    # pos.y is the anchor in API coords already flipped; valign semantics:
    # baseline: pos is baseline; box spans [pos.y - height, pos.y + depth]
    # top:    pos is top;    box spans [pos.y, pos.y + height + depth]
    # bottom: pos is bottom; box spans [pos.y - height - depth, pos.y]
    # center: centered
    if va == "baseline":
        y = pos.y - h
    elif va == "top":
        y = pos.y
    elif va == "bottom":
        y = pos.y - h - d
    else:  # center
        y = pos.y - (h + d) / 2
    return Box(x, y, w, h + d)


# ---------------------------------------------------------------------------


@dataclass
class SemObj:
    id: str
    kind: str
    el: etree._Element
    meta: dict
    layer: str | None = None

    def bbox(self, doc: IpeDoc) -> Box | None:
        raise NotImplementedError


@dataclass
class NodeObj(SemObj):
    shape: str = "rect"
    body: etree._Element | None = None
    label: etree._Element | None = None
    group_el: etree._Element | None = None
    unsupported_transform: bool = False

    def subpaths(self, doc: IpeDoc) -> list[geo.Subpath]:
        if self.body is None:
            return []
        return path_subpaths(self.body, doc.page_size[1])

    def bbox(self, doc: IpeDoc) -> Box | None:
        if self.body is None:
            return None
        return _bbox_to_box(path_bbox_api(self.body, doc.page_size[1]))

    def label_box(self, doc: IpeDoc) -> Box | None:
        if self.label is None:
            return None
        return text_box_api(self.label, doc.page_size[1])


@dataclass
class TextObj(SemObj):
    text_el: etree._Element | None = None

    def _tel(self) -> etree._Element:
        return self.text_el if self.text_el is not None else self.el

    def bbox(self, doc: IpeDoc) -> Box | None:
        return text_box_api(self._tel(), doc.page_size[1])

    @property
    def content(self) -> str:
        return self._tel().text or ""


@dataclass
class PathObj(SemObj):
    path_el: etree._Element | None = None
    obstacle: bool = False

    def _pel(self) -> etree._Element:
        return self.path_el if self.path_el is not None else self.el

    def bbox(self, doc: IpeDoc) -> Box | None:
        return _bbox_to_box(path_bbox_api(self._pel(), doc.page_size[1]))


@dataclass
class EdgeObj(SemObj):
    path_el: etree._Element | None = None
    label_el: etree._Element | None = None
    source: dict | None = None   # {node, side, offset}
    target: dict | None = None
    routing: dict | None = None  # {mode, waypoints?}
    needs_route: bool = False

    def bbox(self, doc: IpeDoc) -> Box | None:
        el = self.path_el if self.path_el is not None else self.el
        return _bbox_to_box(path_bbox_api(el, doc.page_size[1]))


@dataclass
class GroupObj(SemObj):
    member_ids: list[str] = field(default_factory=list)

    def bbox(self, doc: IpeDoc) -> Box | None:
        boxes = []
        for child in self.el:
            if child.tag == "path":
                bb = path_bbox_api(child, doc.page_size[1])
                if bb:
                    boxes.append(bb)
            elif child.tag == "text":
                tb = text_box_api(child, doc.page_size[1])
                if tb:
                    boxes.append((tb.x, tb.y, tb.x2, tb.y2))
        if not boxes:
            return None
        return Box(
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes) - min(b[0] for b in boxes),
            max(b[3] for b in boxes) - min(b[1] for b in boxes),
        )


@dataclass
class SceneSnapshot:
    doc: IpeDoc
    objects: dict[str, SemObj] = field(default_factory=dict)
    duplicates: dict[str, list[etree._Element]] = field(default_factory=dict)
    unmanaged: list[etree._Element] = field(default_factory=list)
    doc_meta: dict | None = None

    @property
    def page_w(self) -> float:
        return self.doc.page_size[0]

    @property
    def page_h(self) -> float:
        return self.doc.page_size[1]

    @property
    def edges(self) -> dict[str, "EdgeObj"]:
        return {k: o for k, o in self.objects.items() if isinstance(o, EdgeObj)}

    def get(self, oid: str) -> SemObj | None:
        return self.objects.get(oid)

    def require(self, oid: str) -> SemObj:
        obj = self.objects.get(oid)
        if obj is None:
            raise IbcError("NOT_FOUND", f"object {oid!r} not found", details={"id": oid})
        return obj

    def edges_touching(self, oid: str) -> list["EdgeObj"]:
        out = []
        for o in self.objects.values():
            if isinstance(o, EdgeObj):
                for ep in (o.source, o.target):
                    if ep and ep.get("node") == oid:
                        out.append(o)
                        break
        return out

    def obstacle_boxes(self, exclude_ids: set[str]) -> list[tuple[float, float, float, float]]:
        """BBoxes (API coords x1 y1 x2 y2) of objects that block routing."""
        out = []
        for oid, o in self.objects.items():
            if oid in exclude_ids:
                continue
            if isinstance(o, (NodeObj, TextObj)) or (isinstance(o, PathObj) and o.obstacle):
                bb = o.bbox(self.doc)
                if bb:
                    out.append((bb.x, bb.y, bb.x2, bb.y2))
        return out


def _part_map(el: etree._Element) -> dict[str, etree._Element]:
    """part-name -> child element, via ibc1 part metadata."""
    parts: dict[str, etree._Element] = {}
    for child in el:
        meta = get_custom(child)
        if meta and meta.get("part"):
            parts[meta["part"]] = child
    return parts


def build_snapshot(doc: IpeDoc) -> SceneSnapshot:
    idx = build_index(doc)
    snap = SceneSnapshot(doc=doc, duplicates=idx.duplicates, unmanaged=idx.unmanaged)
    snap.doc_meta = doc.get_doc_meta()
    for oid, el in idx.by_id.items():
        meta = get_custom(el) or {}
        kind = meta.get("kind", "")
        layer = el.get("layer")
        if kind == "node" and el.tag == "group":
            parts = _part_map(el)
            body = parts.get("body")
            label = parts.get("label")
            # unsupported transform: non-translation matrix on group or body
            bad = not (
                geo.mat_is_translation(el_matrix(el))
                and (body is None or geo.mat_is_translation(el_matrix(body)))
            )
            snap.objects[oid] = NodeObj(
                id=oid, kind=kind, el=el, meta=meta, layer=layer,
                shape=meta.get("shape", "rect"),
                body=body, label=label, group_el=el,
                unsupported_transform=bad,
            )
        elif kind == "edge":
            if el.tag == "group":
                parts = _part_map(el)
                path_el, label_el = parts.get("path"), parts.get("label")
            else:
                path_el, label_el = el, None
            snap.objects[oid] = EdgeObj(
                id=oid, kind=kind, el=el, meta=meta, layer=layer,
                path_el=path_el, label_el=label_el,
                source=meta.get("source"), target=meta.get("target"),
                routing=meta.get("routing"),
                needs_route=bool(meta.get("needs_route")),
            )
        elif kind == "text" and el.tag == "text":
            snap.objects[oid] = TextObj(id=oid, kind=kind, el=el, meta=meta, layer=layer, text_el=el)
        elif kind == "path" and el.tag == "path":
            snap.objects[oid] = PathObj(
                id=oid, kind=kind, el=el, meta=meta, layer=layer,
                path_el=el, obstacle=bool(meta.get("obstacle")),
            )
        elif kind == "group" and el.tag == "group":
            members = []
            for child in el:
                cm = get_custom(child)
                if cm and cm.get("id") and ":" not in str(cm["id"]):
                    members.append(cm["id"])
            snap.objects[oid] = GroupObj(
                id=oid, kind=kind, el=el, meta=meta, layer=layer, member_ids=members
            )
        else:
            # managed id but structure no longer matches its kind
            snap.objects[oid] = SemObj(id=oid, kind=kind or "unknown", el=el, meta=meta, layer=layer)
    return snap
