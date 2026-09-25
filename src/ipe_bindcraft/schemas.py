"""Pydantic models — the single source of truth for the tool/operation schema.

`Operation` is a discriminated union on the ``op`` field. The exported JSON
Schema is generated from these models; unknown fields are rejected everywhere
and NaN/Infinity are not valid numbers.
"""

from __future__ import annotations

import json
import math
import re
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

OBJECT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{0,63}$")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
RESERVED_LAYERS = {"ibc-meta"}
RESERVED_LAYER_RE = re.compile(r"^ibc-", re.IGNORECASE)

ObjectId = Annotated[str, Field(pattern=OBJECT_ID_RE.pattern, min_length=1, max_length=64)]
RequestId = Annotated[str, Field(pattern=REQUEST_ID_RE.pattern, min_length=1, max_length=128)]
Revision = Annotated[str, Field(min_length=1, max_length=256)]
DocumentId = Annotated[str, Field(min_length=1, max_length=256)]
Role = Annotated[str, Field(min_length=1, max_length=64)]
StyleId = Annotated[str, Field(min_length=1, max_length=64)]
LayerName = Annotated[str, Field(pattern=OBJECT_ID_RE.pattern, min_length=1, max_length=64)]

PositiveBp = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Bp = Annotated[float, Field(allow_inf_nan=False)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=False)


_API_SPACE = ("API coordinates: top-left origin, +x right, +y down, units bp "
              "(1/72 in). Ipe-native bottom-left y-up conversion is internal.")


class Point(Strict):
    x: Bp = Field(description="horizontal position, " + _API_SPACE)
    y: Bp = Field(description="vertical position (down-positive), " + _API_SPACE)


class Box(Strict):
    x: Bp = Field(description="left edge, " + _API_SPACE)
    y: Bp = Field(description="top edge (down-positive), " + _API_SPACE)
    width: PositiveBp
    height: PositiveBp


class BoxPatch(Strict):
    x: Bp | None = None
    y: Bp | None = None
    width: PositiveBp | None = None
    height: PositiveBp | None = None

    @model_validator(mode="after")
    def _nonempty(self):
        if not self.model_fields_set:
            raise ValueError("patch must set at least one field")
        return self


class Text(Strict):
    mode: Literal["plain", "latex"]
    text: Annotated[str, Field(min_length=1, max_length=8192)]
    font_size_bp: PositiveBp | None = None


class TextPatch(Strict):
    mode: Literal["plain", "latex"] | None = None
    text: Annotated[str, Field(min_length=1, max_length=8192)] | None = None
    font_size_bp: PositiveBp | None = None

    @model_validator(mode="after")
    def _nonempty(self):
        if not self.model_fields_set:
            raise ValueError("patch must set at least one field")
        return self


class Port(Strict):
    side: Literal["north", "east", "south", "west", "auto"]
    offset: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None = None

    @model_validator(mode="after")
    def _offset_only_when_pinned(self):
        if self.side == "auto" and self.offset is not None:
            raise ValueError("offset is not allowed on an auto port")
        return self


class Endpoint(Strict):
    node: ObjectId
    port: Port | None = None


class RoutingAuto(Strict):
    mode: Literal["straight", "orthogonal", "auto"]


class RoutingManual(Strict):
    mode: Literal["manual"]
    waypoints: Annotated[list[Point], Field(min_length=1, max_length=64)]


Routing = Annotated[RoutingAuto | RoutingManual, Field(discriminator="mode")]


# --- path segments -----------------------------------------------------------

class SegMove(Strict):
    cmd: Literal["M"]
    x: Bp
    y: Bp


class SegLine(Strict):
    cmd: Literal["L"]
    x: Bp
    y: Bp


class SegCubic(Strict):
    cmd: Literal["C"]
    x1: Bp
    y1: Bp
    x2: Bp
    y2: Bp
    x: Bp
    y: Bp


class SegClose(Strict):
    cmd: Literal["Z"]


Segment = Annotated[SegMove | SegLine | SegCubic | SegClose, Field(discriminator="cmd")]
Segments = Annotated[list[Segment], Field(min_length=2, max_length=1024)]


def validate_segments(segs: list[Segment]) -> None:
    """Semantic check beyond schema: must start with M; Z only inside."""
    if not isinstance(segs[0], SegMove):
        raise ValueError("path must start with an M segment")


# --- operations ---------------------------------------------------------------

class NodeCreate(Strict):
    op: Literal["node.create"]
    id: ObjectId
    shape: Literal["rect", "rounded_rect", "ellipse", "diamond"]
    box: Box = Field(description="absolute placement, " + _API_SPACE)
    label: Text
    role: Role | None = None
    style_id: StyleId | None = None
    corner_radius_bp: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None

    @model_validator(mode="after")
    def _radius_only_rounded(self):
        if self.shape != "rounded_rect" and self.corner_radius_bp is not None:
            raise ValueError("corner_radius_bp only valid for rounded_rect")
        return self


class TextCreate(Strict):
    op: Literal["text.create"]
    id: ObjectId
    x: Bp = Field(description=_API_SPACE)
    y: Bp = Field(description=_API_SPACE)
    text: Text
    role: Role | None = None


class PathCreate(Strict):
    op: Literal["path.create"]
    id: ObjectId
    segments: Segments
    stroke_width_bp: PositiveBp | None = None
    role: Role | None = None
    obstacle: bool = False

    @field_validator("segments")
    @classmethod
    def _check(cls, v):
        validate_segments(v)
        return v


class EdgeCreate(Strict):
    op: Literal["edge.create"]
    id: ObjectId
    source: Endpoint
    target: Endpoint
    routing: Routing
    label: Text | None = None
    role: Role | None = None


class IconCreate(Strict):
    """Curated native-path icon (see list_presets(kind='icon'))."""
    op: Literal["icon.create"]
    id: ObjectId
    name: Annotated[str, Field(min_length=1, max_length=64)]
    box: Box
    role: Role | None = None


class NodeUpdateChanges(Strict):
    box: BoxPatch | None = Field(
        default=None,
        description="absolute box (move/resize), " + _API_SPACE +
                    " Prefer over objects.translate when targeting a position.")
    label: TextPatch | None = None
    role: Role | None = None
    style_id: StyleId | None = None

    @model_validator(mode="after")
    def _nonempty(self):
        if not self.model_fields_set:
            raise ValueError("patch must set at least one field")
        return self


class NodeUpdate(Strict):
    op: Literal["node.update"]
    id: ObjectId
    changes: NodeUpdateChanges


class TextUpdateChanges(Strict):
    x: Bp | None = None
    y: Bp | None = None
    text: TextPatch | None = None
    role: Role | None = None

    @model_validator(mode="after")
    def _nonempty(self):
        if not self.model_fields_set:
            raise ValueError("patch must set at least one field")
        return self


class TextUpdate(Strict):
    op: Literal["text.update"]
    id: ObjectId
    changes: TextUpdateChanges


class PathUpdateChanges(Strict):
    segments: Segments | None = None
    stroke_width_bp: PositiveBp | None = None
    role: Role | None = None
    obstacle: bool | None = None

    @field_validator("segments")
    @classmethod
    def _check(cls, v):
        if v is not None:
            validate_segments(v)
        return v

    @model_validator(mode="after")
    def _nonempty(self):
        if not self.model_fields_set:
            raise ValueError("patch must set at least one field")
        return self


class PathUpdate(Strict):
    op: Literal["path.update"]
    id: ObjectId
    changes: PathUpdateChanges


class EdgeUpdateChanges(Strict):
    source: Endpoint | None = None
    target: Endpoint | None = None
    routing: Routing | None = None
    # `{"label": null}` explicitly clears the label; absent label leaves it.
    # Distinguish via model_fields_set at apply time.
    label: TextPatch | None = None
    role: Role | None = None

    @model_validator(mode="after")
    def _nonempty(self):
        if not self.model_fields_set:
            raise ValueError("patch must set at least one field")
        return self


class EdgeUpdate(Strict):
    op: Literal["edge.update"]
    id: ObjectId
    changes: EdgeUpdateChanges


class ObjectsTranslate(Strict):
    op: Literal["objects.translate"]
    ids: Annotated[list[ObjectId], Field(min_length=1, max_length=500)]
    dx: Bp = Field(description="relative horizontal move (+x right, bp)")
    dy: Bp = Field(
        description="relative vertical move, +y DOWN in API coords "
                    "(positive dy moves objects visually downward). For "
                    "absolute placement use objects.move_to or node.update.box.")

    @field_validator("ids")
    @classmethod
    def _unique(cls, v):
        if len(set(v)) != len(v):
            raise ValueError("duplicate ids in selection")
        return v


class ObjectsMoveTo(Strict):
    """Absolute-position move: places each object's bbox anchor at (x, y)."""

    op: Literal["objects.move_to"]
    ids: Annotated[list[ObjectId], Field(min_length=1, max_length=500)]
    x: Bp = Field(description="target x, " + _API_SPACE)
    y: Bp = Field(description="target y, " + _API_SPACE)
    anchor: Literal["top_left", "center"] = Field(
        default="top_left",
        description="which point of each object's bbox lands on (x, y)")

    @field_validator("ids")
    @classmethod
    def _unique(cls, v):
        if len(set(v)) != len(v):
            raise ValueError("duplicate ids in selection")
        return v


class ObjectsDelete(Strict):
    op: Literal["objects.delete"]
    ids: Annotated[list[ObjectId], Field(min_length=1, max_length=500)]
    cascade_edges: bool = False

    @field_validator("ids")
    @classmethod
    def _unique(cls, v):
        if len(set(v)) != len(v):
            raise ValueError("duplicate ids in selection")
        return v


class ObjectsGroup(Strict):
    op: Literal["objects.group"]
    id: ObjectId
    ids: Annotated[list[ObjectId], Field(min_length=2, max_length=500)]

    @field_validator("ids")
    @classmethod
    def _unique(cls, v):
        if len(set(v)) != len(v):
            raise ValueError("duplicate ids in selection")
        return v


class ObjectsUngroup(Strict):
    op: Literal["objects.ungroup"]
    id: ObjectId


class LayerCreate(Strict):
    op: Literal["layer.create"]
    name: LayerName
    visible: bool = True
    locked: bool = False


Operation = Annotated[
    NodeCreate
    | TextCreate
    | PathCreate
    | EdgeCreate
    | IconCreate
    | NodeUpdate
    | TextUpdate
    | PathUpdate
    | EdgeUpdate
    | ObjectsTranslate
    | ObjectsMoveTo
    | ObjectsDelete
    | ObjectsGroup
    | ObjectsUngroup
    | LayerCreate,
    Field(discriminator="op"),
]


# --- tool call payloads --------------------------------------------------------

class ApplyOperationsInput(Strict):
    document_id: DocumentId
    expected_revision: Revision
    request_id: RequestId
    operations: Annotated[list[Operation], Field(min_length=1, max_length=200)]
    dry_run: bool = False


class CreateDocumentInput(Strict):
    path: str
    width_bp: PositiveBp
    height_bp: PositiveBp
    style_id: StyleId
    tex_profile: str = "paper-serif"
    palette: str | None = None
    template: str | None = None


class OpenDocumentInput(Strict):
    path: str
    backend: Literal["file", "live"] = "file"
    bridge_session_id: str | None = None

    @model_validator(mode="after")
    def _live_needs_session(self):
        if self.backend == "live" and not self.bridge_session_id:
            raise ValueError("live backend requires bridge_session_id")
        return self


class CloseDocumentInput(Strict):
    document_id: DocumentId


class InspectDocumentInput(Strict):
    document_id: DocumentId
    ids: list[ObjectId] | None = None
    include_geometry: bool = True  # API-space boxes are cheap and essential


class LayoutObjectsInput(Strict):
    document_id: DocumentId
    expected_revision: Revision
    request_id: RequestId
    ids: Annotated[list[ObjectId], Field(min_length=1, max_length=500)]
    mode: Literal["align", "distribute", "grid"]
    options: dict | None = None
    dry_run: bool = True


class RouteEdgesInput(Strict):
    document_id: DocumentId
    expected_revision: Revision
    request_id: RequestId
    edge_ids: Annotated[list[ObjectId], Field(min_length=1, max_length=500)]
    dry_run: bool = False


class LintFigureInput(Strict):
    document_id: DocumentId
    revision: Revision | None = None
    target_width_bp: PositiveBp | None = None


class PolishFigureInput(Strict):
    document_id: DocumentId
    expected_revision: Revision
    request_id: RequestId
    fixes: Annotated[list[str], Field(min_length=1)]
    dry_run: bool = True


class RenderPreviewInput(Strict):
    document_id: DocumentId
    revision: Revision
    dpi: Annotated[int, Field(ge=36, le=600)] = 150
    overlay: bool = False


class ExportFigureInput(Strict):
    document_id: DocumentId
    revision: Revision
    output_dir: str
    basename: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")]
    formats: Annotated[list[Literal["pdf", "svg", "png"]], Field(min_length=1)]
    crop_mode: Literal["page"] = "page"
    dpi: Annotated[int, Field(ge=72, le=600)] = 300


class SaveDocumentInput(Strict):
    document_id: DocumentId
    expected_revision: Revision
    request_id: RequestId
    path: str | None = None


class GetRequestStatusInput(Strict):
    document_id: DocumentId
    request_id: RequestId


class ListPresetsInput(Strict):
    kind: Literal["style", "tex", "template", "icon", "palette"]


def finite_json_loads(s: str | bytes) -> object:
    """json.loads that rejects NaN/Infinity literals (non-standard JSON)."""
    def _reject(x):
        raise ValueError(f"non-standard JSON number {x}")

    return json.loads(s, parse_constant=_reject)


def export_schema() -> dict:
    """JSON Schema for apply_operations input, generated from the models."""
    return ApplyOperationsInput.model_json_schema(
        ref_template="#/$defs/{model}", mode="validation"
    )


def exported_schema_json() -> str:
    return json.dumps(export_schema(), indent=2, ensure_ascii=False)


def check_finite_recursive(obj) -> None:
    """Defense in depth: reject non-finite floats in raw payloads."""
    if isinstance(obj, float) and not math.isfinite(obj):
        raise ValueError("non-finite number in payload")
    if isinstance(obj, dict):
        for v in obj.values():
            check_finite_recursive(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            check_finite_recursive(v)
