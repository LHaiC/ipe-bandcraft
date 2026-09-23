import pytest

from ipe_bindcraft.metadata import (
    PREFIX, decode_meta, doc_meta, encode_meta, is_managed, object_meta, part_meta,
)


def test_roundtrip():
    meta = {"id": "n1", "kind": "node", "shape": "rect", "extra": {"a": [1, 2]}}
    enc = encode_meta(meta)
    assert enc.startswith(PREFIX)
    assert '"' not in enc and "<" not in enc and "&" not in enc
    dec = decode_meta(enc)
    assert dec["id"] == "n1" and dec["kind"] == "node" and dec["schema"] == 1


def test_foreign_and_invalid():
    assert decode_meta(None) is None
    assert decode_meta("") is None
    assert decode_meta("plain text") is None
    assert decode_meta("ibc1:!!!notb64") is None
    assert decode_meta("ibc1:e30") is None  # '{}' lacks schema field
    assert not is_managed('{"id":"x"}')     # raw JSON is NOT managed


def test_object_and_part_meta():
    assert decode_meta(object_meta("n1", "node", shape="rect"))["shape"] == "rect"
    p = decode_meta(part_meta("n1", "body"))
    assert p["id"] == "n1:body" and p["owner"] == "n1" and p["part"] == "body"
    d = decode_meta(doc_meta(style_id="paper-default"))
    assert d["style_id"] == "paper-default"
