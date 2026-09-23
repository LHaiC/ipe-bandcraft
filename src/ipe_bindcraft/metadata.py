"""Safe metadata codec for Ipe ``custom`` attributes and layer ``data``.

Format: ``ibc1:<base64url(canonical JSON, no padding)>`` — the alphabet has no
quotes, angle brackets, or ampersands, so it survives Ipe's attribute
serialization untouched (verified in M0, see docs/capability-report.md).

Metadata stores identity, semantic relations, and non-visual config only —
never a second authoritative copy of geometry or text.
"""

from __future__ import annotations

import base64
import json
from typing import Any

PREFIX = "ibc1:"
SCHEMA_VERSION = 1

# kinds of managed top-level objects
KINDS = {"node", "text", "path", "edge", "group"}
# part markers written on children of managed groups
PARTS = {"body", "label", "path"}

META_LAYER = "ibc-meta"


def encode_meta(obj: dict[str, Any]) -> str:
    payload = {"schema": SCHEMA_VERSION, **obj}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_meta(value: str | None) -> dict[str, Any] | None:
    """Decode an ibc1 payload; returns None for absent/foreign/invalid values."""
    if not value or not value.startswith(PREFIX):
        return None
    body = value[len(PREFIX):]
    try:
        pad = "=" * (-len(body) % 4)
        data = json.loads(base64.urlsafe_b64decode(body + pad))
    except Exception:  # noqa: BLE001 — corrupted foreign content is just unmanaged
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA_VERSION:
        return None
    return data


def is_managed(value: str | None) -> bool:
    return decode_meta(value) is not None


def object_meta(obj_id: str, kind: str, **extra) -> str:
    """Encode top-level managed-object metadata."""
    return encode_meta({"id": obj_id, "kind": kind, **extra})


def part_meta(owner: str, part: str) -> str:
    """Encode child-part metadata (e.g. node_x:body)."""
    return encode_meta({"id": f"{owner}:{part}", "owner": owner, "part": part})


def doc_meta(**fields) -> str:
    """Encode document-level config stored on the reserved ibc-meta layer."""
    return encode_meta(fields)
