import pytest

from ipe_bindcraft.document import IpeDoc, build_index, get_custom
from ipe_bindcraft.metadata import META_LAYER, object_meta
from ipe_bindcraft import styles


def doc(w=400, h=300):
    return IpeDoc.new(w, h, styles.stylesheet_xml("paper-default", w, h))


def test_new_doc_structure():
    d = doc()
    assert d.page_size == (400, 300)
    layers = [l.name for l in d.layers()]
    assert "alpha" in layers and META_LAYER in layers
    assert d.get_doc_meta() is None
    d.set_doc_meta(style_id="paper-default")
    assert d.get_doc_meta()["style_id"] == "paper-default"


def test_unknown_elements_preserved():
    d = doc()
    from lxml import etree
    weird = etree.SubElement(d.page, "image")
    weird.set("id", "42")
    weird.text = "deadbeef"
    blob = d.serialize()
    d2 = IpeDoc.parse(blob)
    imgs = d2.page.findall("image")
    assert len(imgs) == 1 and imgs[0].text == "deadbeef"


def test_duplicate_ids_reported_not_silent():
    d = doc()
    from lxml import etree
    for _ in range(2):
        el = etree.SubElement(d.page, "path")
        el.set("custom", object_meta("dup", "path"))
        el.text = "0 0 m\n1 1 l"
    idx = build_index(d)
    assert "dup" in idx.duplicates
    assert len(idx.duplicates["dup"]) == 2


def test_managed_children_inside_plain_group_indexed():
    d = doc()
    from lxml import etree
    g = etree.SubElement(d.page, "group")  # no custom -> human-made group
    inner = etree.SubElement(g, "path")
    inner.set("custom", object_meta("inner1", "path"))
    inner.text = "0 0 m\n1 1 l"
    idx = build_index(d)
    assert "inner1" in idx.by_id


def test_multipage_rejected():
    xml = (b'<?xml version="1.0"?><ipe version="70218">'
           b"<page/><page/></ipe>")
    with pytest.raises(Exception) as e:
        IpeDoc.parse(xml)
    assert "UNSUPPORTED" in str(e.value) or getattr(e.value, "code", "") == "UNSUPPORTED_DOCUMENT_FEATURE"


def test_entity_attack_blocked():
    xml = (b'<?xml version="1.0"?>\n'
           b'<!DOCTYPE ipe [<!ENTITY x "boom">]>\n'
           b'<ipe version="70218"><page><text pos="0 0">&x;</text></page></ipe>')
    d = IpeDoc.parse(xml)
    # entity must NOT be resolved
    t = d.page.find("text")
    assert t is None or "boom" not in (t.text or "")
