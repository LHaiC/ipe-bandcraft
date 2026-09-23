"""Candidate compiler: semantic operations -> local DOM edits.

The same compiler serves FileBackend and LiveBackend (SPEC 3.3): it mutates a
parsed `IpeDoc` in place to produce a candidate document. The caller decides
whether to publish it to disk or into the GUI.

Geometry is authored directly in Ipe coordinates here (bottom-left origin):
API boxes convert via `box_to_ipe_rect`; points via `to_ipe`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lxml import etree

from . import geometry as geo
from .coordinates import Box, Point, box_to_ipe_rect, fmt, to_ipe
from .document import IpeDoc, get_custom, set_custom
from .errors import IbcError, OpError
from .metadata import encode_meta, object_meta, part_meta
from .schemas import (
    EdgeCreate,
    EdgeUpdate,
    IconCreate,
    LayerCreate,
    NodeCreate,
    NodeUpdate,
    ObjectsDelete,
    ObjectsGroup,
    ObjectsTranslate,
    ObjectsUngroup,
    Operation,
    PathCreate,
    PathUpdate,
    TextCreate,
    TextUpdate,
)
from .snapshot import (
    EdgeObj,
    GroupObj,
    NodeObj,
    PathObj,
    SceneSnapshot,
    SemObj,
    TextObj,
    build_snapshot,
    text_pos_api,
)
from .styles import STYLE_DEFAULTS

LATEX_ESCAPES = {
    "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
    "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    "\\": r"\textbackslash{}",
}


def latex_escape(text: str) -> str:
    """plain-mode text -> LaTeX source (layer-1 escaping; XML is layer 2)."""
    out = []
    for ch in text:
        if ch == "\n":
            out.append("\n")  # newline handled by caller's multiline template
        else:
            out.append(LATEX_ESCAPES.get(ch, ch))
    return "".join(out)


def _multiline(latex_src: str) -> str:
    """Explicit newlines -> a multi-line LaTeX construct (tabular)."""
    lines = latex_src.split("\n")
    if len(lines) == 1:
        return latex_src
    inner = r" \\ ".join(lines)
    return r"\begin{tabular}{@{}c@{}}" + inner + r"\end{tabular}"


def text_to_latex(mode: str, text: str) -> str:
    if mode == "plain":
        return _multiline(latex_escape(text))
    return text  # latex mode: caller supplies legal LaTeX, no rewriting


# ---------------------------------------------------------------------------
# element builders
# ---------------------------------------------------------------------------

def _rect_body(b: Box, page_h: float) -> str:
    x1, y1, x2, y2 = box_to_ipe_rect(b, page_h)
    return f"{fmt(x1)} {fmt(y1)} m\n{x2} {fmt(y1)} l\n{fmt(x2)} {fmt(y2)} l\n{fmt(x1)} {fmt(y2)} l\nh"


def _rounded_rect_body(b: Box, page_h: float, r: float) -> str:
    x1, y1, x2, y2 = box_to_ipe_rect(b, page_h)
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    def arc(cx, cy):  # ellipse matrix for radius r at (cx,cy)
        return f"{fmt(r)} 0 0 {fmt(r)} {fmt(cx)} {fmt(cy)}"
    return "\n".join([
        f"{fmt(x1 + r)} {fmt(y1)} m",
        f"{fmt(x2 - r)} {fmt(y1)} l",
        f"{arc(x2 - r, y1 + r)} {fmt(x2)} {fmt(y1 + r)} a",
        f"{fmt(x2)} {fmt(y2 - r)} l",
        f"{arc(x2 - r, y2 - r)} {fmt(x2 - r)} {fmt(y2)} a",
        f"{fmt(x1 + r)} {fmt(y2)} l",
        f"{arc(x1 + r, y2 - r)} {fmt(x1)} {fmt(y2 - r)} a",
        f"{fmt(x1)} {fmt(y1 + r)} l",
        f"{arc(x1 + r, y1 + r)} {fmt(x1 + r)} {fmt(y1)} a",
        "h",
    ])


def _ellipse_body(b: Box, page_h: float) -> str:
    x1, y1, x2, y2 = box_to_ipe_rect(b, page_h)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    rx, ry = (x2 - x1) / 2, (y2 - y1) / 2
    return f"{fmt(rx)} 0 0 {fmt(ry)} {fmt(cx)} {fmt(cy)} e"


def _diamond_body(b: Box, page_h: float) -> str:
    x1, y1, x2, y2 = box_to_ipe_rect(b, page_h)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    return f"{fmt(cx)} {fmt(y1)} m\n{fmt(x2)} {fmt(cy)} l\n{fmt(cx)} {fmt(y2)} l\n{fmt(x1)} {fmt(cy)} l\nh"


def node_body_path(shape: str, b: Box, page_h: float, corner_r: float = 3.0) -> str:
    if shape == "rect":
        return _rect_body(b, page_h)
    if shape == "rounded_rect":
        return _rounded_rect_body(b, page_h, corner_r)
    if shape == "ellipse":
        return _ellipse_body(b, page_h)
    if shape == "diamond":
        return _diamond_body(b, page_h)
    raise ValueError(f"unknown shape {shape}")


def make_text_el(oid: str, pos_api: Point, latex: str, page_h: float, *,
                 size_bp: float, halign: str, valign: str,
                 part_of: str | None = None) -> etree._Element:
    el = etree.Element("text")
    ip = to_ipe(pos_api, page_h)
    el.set("pos", f"{fmt(ip.x)} {fmt(ip.y)}")
    el.set("stroke", "black")
    el.set("type", "label")
    el.set("size", fmt(size_bp))
    el.set("halign", halign)
    el.set("valign", valign)
    el.set("transformations", "translations")
    el.text = latex
    if part_of:
        el.set("custom", part_meta(part_of, "label"))
    else:
        el.set("custom", object_meta(oid, "text"))
    return el


def make_path_el(body: str, *, stroke: str, pen: str, fill: str | None = None,
                 arrow: bool = False, custom: str | None = None,
                 layer: str | None = None) -> etree._Element:
    el = etree.Element("path")
    el.set("stroke", stroke)
    el.set("pen", pen)
    if fill:
        el.set("fill", fill)
    if arrow:
        el.set("arrow", "normal/ibc-arrow")
    if layer:
        el.set("layer", layer)
    if custom:
        el.set("custom", custom)
    el.text = "\n" + body + "\n"
    return el


# ---------------------------------------------------------------------------
# compile context
# ---------------------------------------------------------------------------

@dataclass
class CompileResult:
    changed_ids: list[str] = field(default_factory=list)
    effects: dict = field(default_factory=lambda: {"created": 0, "updated": 0, "deleted": 0, "rerouted": 0})
    text_dirty: bool = False          # caller must run LaTeX measurement
    warnings: list[dict] = field(default_factory=list)


class Compiler:
    def __init__(self, doc: IpeDoc, snap: SceneSnapshot):
        self.doc = doc
        self.snap = snap
        self.page_h = doc.page_size[1]
        self.page_w = doc.page_size[0]
        meta = doc.get_doc_meta() or {}
        self.style_id = meta.get("style_id", "paper-default")
        self.params = STYLE_DEFAULTS.get(self.style_id, STYLE_DEFAULTS["paper-default"])
        self.palette = meta.get("palette", self.params["palette"])
        self.res = CompileResult()
        self._batch_new: set[str] = set()

    # ---- helpers -----------------------------------------------------------

    def _fail(self, op_index: int, code: str, msg: str, ids=None, details=None):
        raise OpError(code, msg, op_index=op_index, object_ids=ids, details=details)

    def _require_obj(self, op_index: int, oid: str):
        obj = self.snap.get(oid)
        if obj is None:
            self._fail(op_index, "NOT_FOUND", f"object {oid!r} not found", [oid])
        return obj

    def _active_layer(self) -> str | None:
        view = self.doc.page.find("view")
        return view.get("active") if view is not None else None

    def _endpoint(self, op_index: int, ep) -> dict:
        """Validate endpoint -> {node, side, offset}; node may be batch-created."""
        node_id = ep.node
        obj = self.snap.get(node_id)
        if obj is None and node_id not in self._batch_new:
            self._fail(op_index, "DANGLING_EDGE",
                       f"endpoint node {node_id!r} does not exist", [node_id])
        if obj is not None and not isinstance(obj, NodeObj):
            self._fail(op_index, "INVALID_OPERATION",
                       f"edge endpoint {node_id!r} is a {obj.kind}, not a node", [node_id])
        side = ep.port.side if ep.port else "auto"
        offset = ep.port.offset if ep.port else None
        if isinstance(obj, NodeObj) and obj.shape in ("ellipse", "diamond") and offset not in (None, 0.5):
            self._fail(op_index, "INVALID_OPERATION",
                       f"{obj.shape} port offset other than 0.5 is not supported in v0.1",
                       [node_id])
        return {"node": node_id, "side": side, "offset": offset}

    def port_abs(self, node: NodeObj, side: str, offset: float | None) -> Point:
        subs = node.subpaths(self.doc)
        bb = node.bbox(self.doc)
        if not subs or bb is None:
            raise IbcError("INVALID_OPERATION", f"node {node.id!r} has no computable outline")
        bbox = (bb.x, bb.y, bb.x2, bb.y2)
        x, y = geo.port_point(subs, bbox, side, offset)
        return Point(x, y)

    # ---- main entry ---------------------------------------------------------

    def apply(self, ops: list[Operation]) -> CompileResult:
        # phase 1: collect ids created in this batch (forward-reference support)
        create_types = (NodeCreate, TextCreate, PathCreate, EdgeCreate,
                        IconCreate, ObjectsGroup, LayerCreate)
        batch_new: set[str] = set()
        for i, op in enumerate(ops):
            if not isinstance(op, create_types):
                continue
            nid = getattr(op, "id", None) or getattr(op, "name", None)
            if nid:
                if nid in batch_new:
                    self._fail(i, "DUPLICATE_ID", f"id {nid!r} created twice in batch", [nid])
                if nid in self.snap.objects or nid in self.snap.duplicates:
                    self._fail(i, "DUPLICATE_ID", f"id {nid!r} already exists", [nid])
                batch_new.add(nid)
        self._batch_new = batch_new

        # phase 2: validate edge endpoints against snapshot ∪ batch ids
        for i, op in enumerate(ops):
            if isinstance(op, EdgeCreate):
                for ep in (op.source, op.target):
                    n = ep.node
                    if n not in self.snap.objects and n not in batch_new:
                        self._fail(i, "DANGLING_EDGE",
                                   f"endpoint node {n!r} does not exist", [n])
            if isinstance(op, EdgeUpdate):
                for ep in (op.changes.source, op.changes.target):
                    if ep is not None and ep.node not in self.snap.objects and ep.node not in batch_new:
                        self._fail(i, "DANGLING_EDGE",
                                   f"endpoint node {ep.node!r} does not exist", [ep.node])

        # phase 3: apply in order; created objects register into the snapshot
        # so later ops in the same batch can reference them.
        for i, op in enumerate(ops):
            self._apply_one(i, op)
        return self.res

    # ---- per-op implementations ---------------------------------------------

    def _apply_one(self, i: int, op: Operation):
        handler = {
            NodeCreate: self._node_create,
            TextCreate: self._text_create,
            PathCreate: self._path_create,
            EdgeCreate: self._edge_create,
            IconCreate: self._icon_create,
            NodeUpdate: self._node_update,
            TextUpdate: self._text_update,
            PathUpdate: self._path_update,
            EdgeUpdate: self._edge_update,
            ObjectsTranslate: self._objects_translate,
            ObjectsDelete: self._objects_delete,
            ObjectsGroup: self._objects_group,
            ObjectsUngroup: self._objects_ungroup,
            LayerCreate: self._layer_create,
        }[type(op)]
        handler(i, op)

    def _node_create(self, i: int, op: NodeCreate):
        b = Box(op.box.x, op.box.y, op.box.width, op.box.height)
        if not b.inside_page(self.page_w, self.page_h):
            self._fail(i, "VALIDATION",
                       f"node box {b} outside page {self.page_w}x{self.page_h}", [op.id])
        fill_token = {"compute": "fill_compute", "memory": "fill_memory", "data": "fill_data",
                      "control": "fill_control", "io": "fill_io"}.get(op.role or "", "fill_data")
        body = make_path_el(
            node_body_path(op.shape, b, self.page_h,
                           op.corner_radius_bp or self.params["corner_radius_bp"]),
            stroke="ibc-stroke", pen="ibc-main",
            fill=f"ibc-{fill_token}",
            custom=part_meta(op.id, "body"),
        )
        label = make_text_el(
            op.id, Point(b.cx, b.cy), text_to_latex(op.label.mode, op.label.text),
            self.page_h, size_bp=op.label.font_size_bp or self.params["label_bp"],
            halign="center", valign="center", part_of=op.id,
        )
        grp = etree.Element("group")
        grp.set("custom", object_meta(op.id, "node", shape=op.shape, role=op.role or "data"))
        grp.append(body)
        grp.append(label)
        layer = self._active_layer()
        if layer:
            grp.set("layer", layer)
        self.doc.page.append(grp)
        self.snap.objects[op.id] = NodeObj(
            id=op.id, kind="node", el=grp,
            meta={"id": op.id, "kind": "node", "shape": op.shape, "role": op.role or "data"},
            layer=layer, shape=op.shape, body=body, label=label, group_el=grp,
        )
        self.res.changed_ids.append(op.id)
        self.res.effects["created"] += 1
        self.res.text_dirty = True

    def _text_create(self, i: int, op: TextCreate):
        latex = text_to_latex(op.text.mode, op.text.text)
        el = make_text_el(
            op.id, Point(op.x, op.y), latex, self.page_h,
            size_bp=op.text.font_size_bp or self.params["label_bp"],
            halign="left", valign="top",
        )
        layer = self._active_layer()
        if layer:
            el.set("layer", layer)
        self.doc.page.append(el)
        self.snap.objects[op.id] = TextObj(
            id=op.id, kind="text", el=el,
            meta={"id": op.id, "kind": "text", "mode": op.text.mode, "role": op.role or ""},
            layer=layer, text_el=el,
        )
        self.res.changed_ids.append(op.id)
        self.res.effects["created"] += 1
        self.res.text_dirty = True

    def _path_create(self, i: int, op: PathCreate):
        body = self._segments_body(op.segments)
        el = make_path_el(
            body, stroke="ibc-stroke",
            pen=fmt(op.stroke_width_bp) if op.stroke_width_bp else "ibc-thin",
            custom=object_meta(op.id, "path", role=op.role or "", obstacle=op.obstacle),
        )
        layer = self._active_layer()
        if layer:
            el.set("layer", layer)
        self.doc.page.append(el)
        self.snap.objects[op.id] = PathObj(
            id=op.id, kind="path", el=el,
            meta={"id": op.id, "kind": "path", "role": op.role or "", "obstacle": op.obstacle},
            layer=layer, path_el=el, obstacle=op.obstacle,
        )
        self.res.changed_ids.append(op.id)
        self.res.effects["created"] += 1

    def _edge_create(self, i: int, op: EdgeCreate):
        src = self._endpoint(i, op.source)
        dst = self._endpoint(i, op.target)
        routing = {"mode": op.routing.mode}
        if op.routing.mode == "manual":
            routing["waypoints"] = [[p.x, p.y] for p in op.routing.waypoints]
        meta = object_meta(
            op.id, "edge", role=op.role or "",
            source=src, target=dst, routing=routing, needs_route=True,
        )
        if op.label is not None:
            grp = etree.Element("group")
            grp.set("custom", meta)
            path_el = make_path_el("0 0 m\n0 0 l", stroke="ibc-edge", pen="ibc-main",
                                   arrow=True, custom=part_meta(op.id, "path"))
            lbl = make_text_el(
                op.id, Point(0, 0), text_to_latex(op.label.mode, op.label.text),
                self.page_h, size_bp=op.label.font_size_bp or self.params["secondary_bp"],
                halign="center", valign="bottom", part_of=op.id,
            )
            grp.append(path_el)
            grp.append(lbl)
            el = grp
            self.res.text_dirty = True
        else:
            el = make_path_el("0 0 m\n0 0 l", stroke="ibc-edge", pen="ibc-main",
                              arrow=True, custom=meta)
        layer = self._active_layer()
        if layer:
            el.set("layer", layer)
        self.doc.page.append(el)
        # register so later ops in this batch can touch the edge
        path_el = None
        label_el = None
        if el.tag == "group":
            for child in el:
                cm = get_custom(child)
                if cm and cm.get("part") == "path":
                    path_el = child
                elif cm and cm.get("part") == "label":
                    label_el = child
        else:
            path_el = el
        self.snap.objects[op.id] = EdgeObj(
            id=op.id, kind="edge", el=el,
            meta={"id": op.id, "kind": "edge", "role": op.role or "",
                  "source": src, "target": dst, "routing": routing, "needs_route": True},
            layer=layer, path_el=path_el, label_el=label_el,
            source=src, target=dst, routing=routing, needs_route=True,
        )
        self.res.changed_ids.append(op.id)
        self.res.effects["created"] += 1

    def _icon_create(self, i: int, op: IconCreate):
        from . import icons
        b = Box(op.box.x, op.box.y, op.box.width, op.box.height)
        if not b.inside_page(self.page_w, self.page_h):
            self._fail(i, "VALIDATION",
                       f"icon box {b} outside page {self.page_w}x{self.page_h}", [op.id])
        try:
            strokes = icons.render_icon(op.name)
        except IbcError as exc:
            self._fail(i, exc.code, exc.message, [op.id], exc.details)
        sx, sy = b.width / 100.0, b.height / 100.0  # design space is 0..100
        fill_token = {"compute": "fill_compute", "memory": "fill_memory",
                      "data": "fill_data", "control": "fill_control",
                      "io": "fill_io"}.get(op.role or "", "fill_data")
        grp = etree.Element("group")
        grp.set("custom", object_meta(op.id, "icon", icon=op.name,
                                      role=op.role or ""))
        for k, st in enumerate(strokes):
            lines = []
            first = True
            for (px, py) in st["points"]:
                ip = to_ipe(Point(b.x + px * sx, b.y + py * sy), self.page_h)
                lines.append(f"{fmt(ip.x)} {fmt(ip.y)} {'m' if first else 'l'}")
                first = False
            if st.get("closed"):
                lines.append("h")  # Ipe 'h' = closepath
            grp.append(make_path_el(
                "\n".join(lines), stroke="ibc-stroke", pen="ibc-thin",
                fill=f"ibc-{fill_token}" if st.get("fill") else None,
                custom=part_meta(op.id, f"p{k}")))
        layer = self._active_layer()
        if layer:
            grp.set("layer", layer)
        self.doc.page.append(grp)
        self.snap.objects[op.id] = SemObj(
            id=op.id, kind="icon", el=grp,
            meta={"id": op.id, "kind": "icon", "icon": op.name,
                  "role": op.role or ""},
            layer=layer)
        self.res.changed_ids.append(op.id)
        self.res.effects["created"] += 1

    # -- updates ---------------------------------------------------------------

    def _node_update(self, i: int, op: NodeUpdate):
        obj = self._require_obj(i, op.id)
        if not isinstance(obj, NodeObj):
            self._fail(i, "INVALID_OPERATION", f"{op.id!r} is not a node", [op.id])
        ch = op.changes
        if ch.box is not None:
            if obj.unsupported_transform:
                self._fail(i, "UNSUPPORTED_TRANSFORM",
                           f"node {op.id!r} carries a non-translation transform", [op.id])
            old = obj.bbox(self.doc)
            new = Box(
                ch.box.x if ch.box.x is not None else old.x,
                ch.box.y if ch.box.y is not None else old.y,
                ch.box.width if ch.box.width is not None else old.width,
                ch.box.height if ch.box.height is not None else old.height,
            )
            if not new.inside_page(self.page_w, self.page_h):
                self._fail(i, "VALIDATION",
                           f"node box would leave the page", [op.id])
            # Bake the group's (translation-only) matrix away: the body path is
            # rewritten in final Ipe coords, so the group matrix must go.
            obj.body.text = "\n" + node_body_path(
                obj.shape, new, self.page_h,
                float(obj.meta.get("corner_radius_bp", self.params["corner_radius_bp"])),
            ) + "\n"
            if "matrix" in obj.el.attrib:
                del obj.el.attrib["matrix"]
            if obj.label is not None:
                # label keeps its visual offset from the old center
                lp = self._label_pos_api(obj)  # API space, includes old grp_m
                nlp = Point(lp.x + (new.cx - old.cx), lp.y + (new.cy - old.cy))
                ip = to_ipe(nlp, self.page_h)
                obj.label.set("pos", f"{fmt(ip.x)} {fmt(ip.y)}")
            for edge in self.snap.edges_touching(op.id):
                self._mark_needs_route(edge)
            self.res.changed_ids.append(op.id)
            self.res.effects["updated"] += 1
        if ch.label is not None:
            if ch.label.text is not None or ch.label.mode is not None:
                mode = ch.label.mode or obj.meta.get("label_mode", "plain")
                text = ch.label.text if ch.label.text is not None else self._label_raw(obj)
                obj.label.text = text_to_latex(mode, text)
                obj.meta["label_mode"] = mode
                obj.el.set("custom", encode_meta(obj.meta))
                self.res.text_dirty = True
            if ch.label.font_size_bp is not None:
                obj.label.set("size", fmt(ch.label.font_size_bp))
                self.res.text_dirty = True
            self.res.changed_ids.append(op.id)
            self.res.effects["updated"] += 1
        if ch.role is not None or ch.style_id is not None:
            if ch.role is not None:
                obj.meta["role"] = ch.role
                fill_token = {"compute": "fill_compute", "memory": "fill_memory",
                              "data": "fill_data", "control": "fill_control",
                              "io": "fill_io"}.get(ch.role, "fill_data")
                obj.body.set("fill", f"ibc-{fill_token}")
                obj.el.set("custom", encode_meta(obj.meta))
            self.res.changed_ids.append(op.id)
            self.res.effects["updated"] += 1

    def _label_pos_api(self, obj: NodeObj) -> Point:
        return text_pos_api(obj.label, self.page_h)

    def _label_raw(self, obj: NodeObj) -> str:
        return obj.label.text or ""

    def _text_update(self, i: int, op: TextUpdate):
        obj = self._require_obj(i, op.id)
        if not isinstance(obj, TextObj):
            self._fail(i, "INVALID_OPERATION", f"{op.id!r} is not a text", [op.id])
        el = obj.text_el if obj.text_el is not None else obj.el
        ch = op.changes
        if ch.x is not None or ch.y is not None:
            cur = text_pos_api(el, self.page_h)
            nx = ch.x if ch.x is not None else cur.x
            ny = ch.y if ch.y is not None else cur.y
            ip = to_ipe(Point(nx, ny), self.page_h)
            el.set("pos", f"{fmt(ip.x)} {fmt(ip.y)}")
        if ch.text is not None:
            mode = ch.text.mode or obj.meta.get("mode", "plain")
            text = ch.text.text if ch.text.text is not None else (el.text or "")
            el.text = text_to_latex(mode, text)
            obj.meta["mode"] = mode
            el.set("custom", encode_meta(obj.meta))
            self.res.text_dirty = True
            if ch.text.font_size_bp is not None:
                el.set("size", fmt(ch.text.font_size_bp))
        if ch.role is not None:
            obj.meta["role"] = ch.role
            el.set("custom", encode_meta(obj.meta))
        self.res.changed_ids.append(op.id)
        self.res.effects["updated"] += 1

    def _path_update(self, i: int, op: PathUpdate):
        obj = self._require_obj(i, op.id)
        if not isinstance(obj, PathObj):
            self._fail(i, "INVALID_OPERATION", f"{op.id!r} is not a path", [op.id])
        el = obj.path_el if obj.path_el is not None else obj.el
        ch = op.changes
        if ch.segments is not None:
            if not geo.mat_is_translation(el_matrix(el)):
                self._fail(i, "UNSUPPORTED_TRANSFORM",
                           f"path {op.id!r} has a non-translation matrix", [op.id])
            el.text = "\n" + self._segments_body(ch.segments) + "\n"
        if ch.stroke_width_bp is not None:
            el.set("pen", fmt(ch.stroke_width_bp))
        if ch.obstacle is not None:
            obj.meta["obstacle"] = ch.obstacle
            el.set("custom", encode_meta(obj.meta))
        if ch.role is not None:
            obj.meta["role"] = ch.role
            el.set("custom", encode_meta(obj.meta))
        self.res.changed_ids.append(op.id)
        self.res.effects["updated"] += 1

    def _edge_update(self, i: int, op: EdgeUpdate):
        obj = self._require_obj(i, op.id)
        if not isinstance(obj, EdgeObj):
            self._fail(i, "INVALID_OPERATION", f"{op.id!r} is not an edge", [op.id])
        ch = op.changes
        meta = dict(obj.meta)
        if ch.source is not None:
            meta["source"] = self._endpoint(i, ch.source)
        if ch.target is not None:
            meta["target"] = self._endpoint(i, ch.target)
        if ch.routing is not None:
            meta["routing"] = {"mode": ch.routing.mode}
            if ch.routing.mode == "manual":
                meta["routing"]["waypoints"] = [[p.x, p.y] for p in ch.routing.waypoints]
        if "label" in ch.model_fields_set:
            if ch.label is None:
                # explicit clear: drop the label child if the edge is a group
                if obj.label_el is not None:
                    obj.el.remove(obj.label_el)
                    obj.label_el = None
            else:
                if obj.label_el is None:
                    lbl = make_text_el(op.id, Point(0, 0), "", self.page_h,
                                       size_bp=self.params["secondary_bp"],
                                       halign="center", valign="bottom", part_of=op.id)
                    if obj.el.tag != "group":
                        # wrap path into a group; path becomes a named part
                        path_el = obj.path_el if obj.path_el is not None else obj.el
                        grp = etree.Element("group")
                        grp.set("custom", obj.el.get("custom"))
                        path_el.getparent().replace(path_el, grp)
                        path_el.set("custom", part_meta(op.id, "path"))
                        grp.append(path_el)
                        obj.el = grp
                        obj.path_el = path_el
                    obj.el.append(lbl)
                    obj.label_el = lbl
                if ch.label.text is not None or ch.label.mode is not None:
                    mode = ch.label.mode or meta.get("label_mode", "plain")
                    text = ch.label.text if ch.label.text is not None else (obj.label_el.text or "")
                    obj.label_el.text = text_to_latex(mode, text)
                    meta["label_mode"] = mode
                if ch.label.font_size_bp is not None:
                    obj.label_el.set("size", fmt(ch.label.font_size_bp))
                self.res.text_dirty = True
        meta["needs_route"] = True
        obj.el.set("custom", encode_meta(meta))
        obj.meta = meta
        obj.source = meta.get("source", obj.source)
        obj.target = meta.get("target", obj.target)
        obj.routing = meta.get("routing", obj.routing)
        self._mark_needs_route(obj)
        self.res.changed_ids.append(op.id)
        self.res.effects["updated"] += 1

    # -- bulk ops ----------------------------------------------------------------

    def _objects_translate(self, i: int, op: ObjectsTranslate):
        if op.dx == 0 and op.dy == 0:
            return  # no-op: does not touch the document
        self._check_set(i, op.ids)
        for oid in op.ids:
            obj = self.snap.get(oid)
            el = obj.el
            m = geo.parse_matrix(el.get("matrix"))
            t = geo.mat_mul(geo.mat_translate(op.dx, -op.dy), m)  # Ipe y flips sign
            el.set("matrix", geo.format_matrix(t))
            for edge in self.snap.edges_touching(oid):
                self._mark_needs_route(edge)
            self.res.changed_ids.append(oid)
            self.res.effects["updated"] += 1

    def _objects_delete(self, i: int, op: ObjectsDelete):
        self._check_set(i, op.ids)
        # reference check
        referenced = []
        for oid in op.ids:
            refs = [e.id for e in self.snap.edges_touching(oid)]
            if refs and not op.cascade_edges:
                referenced.append((oid, refs))
        if referenced:
            self._fail(i, "REFERENCED_OBJECT",
                       "objects have bound edges; pass cascade_edges=true to remove them",
                       details={"referenced": {k: v for k, v in referenced}})
        doomed_edges: list[EdgeObj] = []
        if op.cascade_edges:
            for oid in op.ids:
                doomed_edges.extend(self.snap.edges_touching(oid))
        for e in doomed_edges:
            e.el.getparent().remove(e.el)
            self.snap.objects.pop(e.id, None)
            self.res.effects["deleted"] += 1
        for oid in op.ids:
            obj = self.snap.get(oid)
            obj.el.getparent().remove(obj.el)
            self.snap.objects.pop(oid, None)
            self.res.changed_ids.append(oid)
            self.res.effects["deleted"] += 1

    def _objects_group(self, i: int, op: ObjectsGroup):
        self._check_set(i, op.ids)
        # may not mix a group with its own descendants
        idset = set(op.ids)
        for oid in op.ids:
            for anc in _ancestors(self.snap.get(oid).el):
                meta = get_custom(anc)
                if meta and meta.get("id") in idset:
                    self._fail(i, "OVERLAPPING_SELECTION",
                               f"cannot group {oid!r} together with its ancestor", [oid])
        grp = etree.Element("group")
        grp.set("custom", object_meta(op.id, "group"))
        parent = self.snap.get(op.ids[0]).el.getparent()
        for oid in op.ids:
            el = self.snap.get(oid).el
            el.getparent().remove(el)
            grp.append(el)
        layer = self._active_layer()
        if layer:
            grp.set("layer", layer)
        parent.append(grp)
        self.snap.objects[op.id] = GroupObj(
            id=op.id, kind="group", el=grp,
            meta={"id": op.id, "kind": "group"},
            layer=layer, member_ids=list(op.ids),
        )
        self.res.changed_ids.append(op.id)
        self.res.effects["created"] += 1

    def _objects_ungroup(self, i: int, op: ObjectsUngroup):
        obj = self._require_obj(i, op.id)
        if not isinstance(obj, GroupObj):
            self._fail(i, "INVALID_OPERATION",
                       f"{op.id!r} is not a plain group (node/edge internals are not ungroupable)",
                       [op.id])
        parent = obj.el.getparent()
        pos = list(parent).index(obj.el)
        for child in list(obj.el):
            obj.el.remove(child)
            parent.insert(pos, child)
            pos += 1
        parent.remove(obj.el)
        self.res.changed_ids.append(op.id)
        self.res.effects["deleted"] += 1

    def _layer_create(self, i: int, op: LayerCreate):
        import re

        if re.match(r"(?i)^ibc-", op.name):
            self._fail(i, "INVALID_OPERATION", f"layer name {op.name!r} is reserved", [op.name])
        for lay in self.doc.layers():
            if lay.name == op.name:
                self._fail(i, "DUPLICATE_ID", f"layer {op.name!r} already exists", [op.name])
        view = self.doc.page.find("view")
        el = etree.Element("layer")
        el.set("name", op.name)
        if not op.locked:
            pass
        else:
            el.set("edit", "no")
        if view is not None:
            view.addprevious(el)
            layers = (view.get("layers") or "").split()
            if op.visible:
                layers.append(op.name)
            view.set("layers", " ".join(layers))
        else:
            self.doc.page.insert(0, el)
        self.res.changed_ids.append(op.name)
        self.res.effects["created"] += 1

    # -- shared -----------------------------------------------------------------

    def _check_set(self, i: int, ids: list[str]):
        # group + descendant overlap check
        for oid in ids:
            obj = self._require_obj(i, oid)
            for anc in _ancestors(obj.el):
                meta = get_custom(anc)
                if meta and meta.get("id") in set(ids):
                    self._fail(i, "OVERLAPPING_SELECTION",
                               f"selection contains both {oid!r} and an ancestor", ids)

    def _mark_needs_route(self, edge: EdgeObj):
        meta = dict(edge.meta)
        meta["needs_route"] = True
        edge.el.set("custom", encode_meta(meta))
        edge.meta = meta
        edge.needs_route = True

    def _segments_body(self, segs) -> str:
        out = []
        for s in segs:
            if s.cmd == "M":
                ip = to_ipe(Point(s.x, s.y), self.page_h)
                out.append(f"{fmt(ip.x)} {fmt(ip.y)} m")
            elif s.cmd == "L":
                ip = to_ipe(Point(s.x, s.y), self.page_h)
                out.append(f"{fmt(ip.x)} {fmt(ip.y)} l")
            elif s.cmd == "C":
                p1 = to_ipe(Point(s.x1, s.y1), self.page_h)
                p2 = to_ipe(Point(s.x2, s.y2), self.page_h)
                p3 = to_ipe(Point(s.x, s.y), self.page_h)
                out.append(
                    f"{fmt(p1.x)} {fmt(p1.y)} {fmt(p2.x)} {fmt(p2.y)} {fmt(p3.x)} {fmt(p3.y)} c"
                )
            elif s.cmd == "Z":
                out.append("h")
        return "\n".join(out)


def _ancestors(el: etree._Element):
    cur = el.getparent()
    while cur is not None and cur.tag == "group":
        yield cur
        cur = cur.getparent()


def el_matrix(el: etree._Element) -> geo.Matrix:
    return geo.parse_matrix(el.get("matrix"))
