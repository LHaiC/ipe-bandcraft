"""Preserve-editing Ipe XML document.

Rules enforced here (SPEC 5.3 / 12):
- Parse with a hardened parser: no entity resolution, no network DTD.
- Single page, single view only; anything else -> UNSUPPORTED_DOCUMENT_FEATURE.
- Unknown elements/attributes are never touched; serialization is the same
  DOM tree we parsed (lxml preserves them).
- No namespaces are ever introduced.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from lxml import etree

from .errors import IbcError
from .metadata import META_LAYER, decode_meta

OBJECT_TAGS = {"path", "text", "group", "image", "use"}
DEFAULT_PAPER = (595.276, 841.89)  # A4 in pt/bp, used when no layout exists

_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    dtd_validation=False,
    strip_cdata=False,
    remove_blank_text=False,
    remove_comments=False,
    huge_tree=False,
)


@dataclass
class LayerInfo:
    name: str
    visible: bool = True
    editable: bool = True
    snap: str = "visible"
    data: str | None = None
    el: etree._Element | None = None


class IpeDoc:
    """A parsed .ipe document with a single page."""

    def __init__(self, tree: etree._ElementTree, source: bytes):
        self.tree = tree
        self.root = tree.getroot()
        if self.root.tag != "ipe":
            raise IbcError("VALIDATION", "not an Ipe document")
        self._source = source
        self._page = self._single_page()
        self._check_views()

    # ---- construction ------------------------------------------------------

    @classmethod
    def parse(cls, data: bytes) -> "IpeDoc":
        try:
            tree = etree.ElementTree(etree.fromstring(data, _PARSER))
        except etree.XMLSyntaxError as exc:
            raise IbcError("VALIDATION", f"XML parse failed: {exc}") from exc
        return cls(tree, data)

    @classmethod
    def load(cls, path) -> "IpeDoc":
        from pathlib import Path

        data = Path(path).read_bytes()
        return cls.parse(data)

    @classmethod
    def new(cls, width_bp: float, height_bp: float, style_xml: str,
            preamble: str | None = None, creator: str = "ipe-bindcraft 0.1") -> "IpeDoc":
        """Create a single-page document with the given embedded stylesheet."""
        from .coordinates import fmt

        xml = (
            '<?xml version="1.0"?>\n'
            f'<ipe version="70218" creator="{creator}">\n'
            f"{style_xml}\n"
            + (f"<preamble>{_xml_escape(preamble)}</preamble>\n" if preamble else "")
            + "<page>\n"
            '<layer name="alpha"/>\n'
            f'<layer name="{META_LAYER}" edit="no" snap="never"/>\n'
            '<view layers="alpha" active="alpha"/>\n'
            "</page>\n</ipe>\n"
        )
        doc = cls.parse(xml.encode("utf-8"))
        return doc

    # ---- structure ----------------------------------------------------------

    def _pages(self) -> list[etree._Element]:
        return self.root.findall("page")

    def _single_page(self) -> etree._Element:
        pages = self._pages()
        if len(pages) != 1:
            raise IbcError(
                "UNSUPPORTED_DOCUMENT_FEATURE",
                f"document has {len(pages)} pages; only single-page documents are supported",
                details={"pages": len(pages)},
            )
        return pages[0]

    def _check_views(self) -> None:
        views = self._page.findall("view")
        if len(views) > 1:
            raise IbcError(
                "UNSUPPORTED_DOCUMENT_FEATURE",
                f"page has {len(views)} views; only single-view documents are supported",
                details={"views": len(views)},
            )

    @property
    def page(self) -> etree._Element:
        return self._page

    @property
    def page_size(self) -> tuple[float, float]:
        """(width_bp, height_bp) from the top-most layout, else A4 default."""
        for sheet in reversed(self.root.findall("ipestyle")):
            layout = sheet.find("layout")
            if layout is not None and layout.get("paper"):
                w, h = (float(v) for v in layout.get("paper").split()[:2])
                return w, h
        return DEFAULT_PAPER

    @property
    def source_bytes(self) -> bytes:
        return self._source

    def serialize(self) -> bytes:
        return etree.tostring(
            self.tree, xml_declaration=True, encoding="utf-8", standalone=None
        )

    def clone(self) -> "IpeDoc":
        return IpeDoc.parse(self.serialize())

    def content_hash(self) -> str:
        return hashlib.sha256(self.serialize()).hexdigest()

    # ---- objects ------------------------------------------------------------

    def objects(self) -> list[etree._Element]:
        """Top-level object elements of the page, in document order."""
        return [el for el in self._page if el.tag in OBJECT_TAGS]

    def layers(self) -> list[LayerInfo]:
        view = self._page.find("view")
        visible = set((view.get("layers") or "").split()) if view is not None else None
        out = []
        for el in self._page.findall("layer"):
            name = el.get("name", "")
            out.append(
                LayerInfo(
                    name=name,
                    visible=(name in visible) if visible is not None else True,
                    editable=el.get("edit", "yes") != "no",
                    snap=el.get("snap", "visible"),
                    data=el.get("data"),
                    el=el,
                )
            )
        return out

    def ensure_meta_layer(self) -> etree._Element:
        for el in self._page.findall("layer"):
            if el.get("name") == META_LAYER:
                return el
        # insert after last user layer, before <view>
        layer = etree.SubElement(self._page, "layer")
        layer.set("name", META_LAYER)
        layer.set("edit", "no")
        layer.set("snap", "never")
        # move it before the first <view> to keep the documented order
        view = self._page.find("view")
        if view is not None:
            self._page.remove(layer)
            view.addprevious(layer)
        return layer

    def get_doc_meta(self) -> dict | None:
        for el in self._page.findall("layer"):
            if el.get("name") == META_LAYER:
                return decode_meta(el.get("data"))
        return None

    def set_doc_meta(self, **fields) -> None:
        layer = self.ensure_meta_layer()
        from .metadata import doc_meta

        layer.set("data", doc_meta(**fields))

    def notes(self) -> str | None:
        el = self._page.find("notes")
        return el.text if el is not None else None


# ---- element-level helpers ----------------------------------------------------

def get_custom(el: etree._Element) -> dict | None:
    return decode_meta(el.get("custom"))


def set_custom(el: etree._Element, meta: dict) -> None:
    from .metadata import encode_meta

    el.set("custom", encode_meta(meta))


def object_layer(el: etree._Element) -> str | None:
    return el.get("layer")


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


@dataclass
class IdentityIndex:
    """id -> element for managed top-level objects."""

    by_id: dict[str, etree._Element] = field(default_factory=dict)
    duplicates: dict[str, list[etree._Element]] = field(default_factory=dict)
    unmanaged: list[etree._Element] = field(default_factory=list)

    def add(self, el: etree._Element) -> None:
        meta = get_custom(el)
        if meta is None or "id" not in meta or ":" in str(meta["id"]):
            # no ibc1 meta, or a part marker — not an addressable object.
            # Still descend into groups so members keep their identity
            # (SPEC 12: preserve human edits / regrouping).
            if el.tag == "group":
                for child in el:
                    if child.tag in OBJECT_TAGS:
                        self.add(child)
            else:
                self.unmanaged.append(el)
            return
        oid = str(meta["id"])
        if oid in self.by_id:
            self.duplicates.setdefault(oid, [self.by_id[oid]]).append(el)
        else:
            self.by_id[oid] = el
        # also descend into managed groups (e.g. objects.group results) so
        # member ids remain addressable; part-marked children are filtered
        # above by the ":" check.
        if el.tag == "group":
            for child in el:
                if child.tag in OBJECT_TAGS:
                    self.add(child)


def build_index(doc: IpeDoc) -> IdentityIndex:
    idx = IdentityIndex()
    for el in doc.objects():
        idx.add(el)
    return idx
