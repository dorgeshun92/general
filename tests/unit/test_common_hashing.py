"""scripts/common/hashing.py: known-answer SHA-256 and canonical JSON hashing."""
from __future__ import annotations

import hashlib
import re

from scripts.common.hashing import sha256_bytes, sha256_file, sha256_json

SHA_EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
SHA_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
HEX64 = re.compile(r"^[a-f0-9]{64}$")  # schema's sha256 pattern


class TestSha256Bytes:
    def test_empty(self):
        assert sha256_bytes(b"") == SHA_EMPTY

    def test_abc(self):
        assert sha256_bytes(b"abc") == SHA_ABC

    def test_output_matches_schema_pattern(self):
        assert HEX64.match(sha256_bytes(b"anything"))


class TestSha256File:
    def test_known_answer(self, tmp_path):
        p = tmp_path / "abc.txt"
        p.write_bytes(b"abc")
        assert sha256_file(p) == SHA_ABC

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.bin"
        p.write_bytes(b"")
        assert sha256_file(p) == SHA_EMPTY

    def test_accepts_str_path(self, tmp_path):
        p = tmp_path / "abc.txt"
        p.write_bytes(b"abc")
        assert sha256_file(str(p)) == SHA_ABC

    def test_large_file_is_chunked_correctly(self, tmp_path):
        data = bytes(range(256)) * (5 * 1024 + 7)  # > 1 MiB and not a multiple of the chunk size
        p = tmp_path / "big.bin"
        p.write_bytes(data)
        assert sha256_file(p) == hashlib.sha256(data).hexdigest() == sha256_bytes(data)

    def test_missing_file_raises(self, tmp_path):
        import pytest

        with pytest.raises(FileNotFoundError):
            sha256_file(tmp_path / "missing.bin")


class TestSha256Json:
    def test_key_order_independent(self):
        assert sha256_json({"a": 1, "b": 2}) == sha256_json({"b": 2, "a": 1})

    def test_nested_key_order_independent(self):
        left = {"outer": {"z": [1, {"y": "1", "x": "2"}], "a": None}, "k": True}
        right = {"k": True, "outer": {"a": None, "z": [1, {"x": "2", "y": "1"}]}}
        assert sha256_json(left) == sha256_json(right)

    def test_list_order_matters(self):
        assert sha256_json([1, 2]) != sha256_json([2, 1])

    def test_value_change_changes_hash(self):
        assert sha256_json({"a": "1"}) != sha256_json({"a": "2"})

    def test_canonical_form_has_no_whitespace(self):
        assert sha256_json({"b": 2, "a": [1, 2]}) == sha256_bytes(b'{"a":[1,2],"b":2}')

    def test_type_distinction_preserved(self):
        # A decimal string and a JSON number must not collide.
        assert sha256_json({"amount": "1"}) != sha256_json({"amount": 1})

    def test_non_ascii_is_not_escaped(self):
        assert sha256_json("é") == sha256_bytes('"é"'.encode("utf-8"))

    def test_scalars(self):
        assert sha256_json(None) == sha256_bytes(b"null")
        assert sha256_json(True) == sha256_bytes(b"true")
