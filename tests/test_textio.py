"""Tests for encoding, BOM, line-ending and binary handling (:mod:`sr.textio`).

The invariant that matters: a round trip through decode → modify → encode is
byte-exact for everything sr claims to support, and *nothing* is written for
anything it does not understand.
"""

from __future__ import annotations

import codecs
from pathlib import Path

import pytest

from sr.textio import (
    DecodedFile,
    UndecodableFileError,
    decode_bytes,
    detect_line_ending,
    looks_binary,
    normalize_codec,
    read_text_file,
    sniff_binary,
)


class TestBinarySniffing:
    @pytest.mark.parametrize(
        "data",
        [
            b"",
            b"plain ascii\n",
            "café — unicode\n".encode(),
            "привет мир\n".encode(),
            "cp1252 text".encode("cp1252"),
            b"tabs\tand\x0bvertical\x0ctabs\n",
        ],
    )
    def test_text_is_not_binary(self, data):
        assert sniff_binary(data) is False

    @pytest.mark.parametrize(
        "data",
        [
            b"\x00\x01\x02",
            b"hello\x00world",
            bytes(range(32)),
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 40,
        ],
    )
    def test_binary_is_binary(self, data):
        assert sniff_binary(data) is True

    def test_utf16_without_bom_is_reported_binary(self):
        """Every second byte is NUL, so the sniffer is right to refuse it."""
        assert sniff_binary("hello".encode("utf-16-le")) is True

    def test_sample_limit(self):
        payload = b"text\n" * 10 + b"\x00" * 100
        assert sniff_binary(payload, sample=8) is False

    def test_extension_blocklist(self, tmp_path: Path):
        png = tmp_path / "image.png"
        png.write_bytes(b"not really a png but the name says so")
        assert looks_binary(png) is True

    def test_empty_file_is_text(self, tmp_path: Path):
        empty = tmp_path / "empty.txt"
        empty.write_bytes(b"")
        assert looks_binary(empty) is False


class TestLineEndings:
    @pytest.mark.parametrize(
        "data,expected,mixed",
        [
            (b"a\nb\n", "\n", False),
            (b"a\r\nb\r\n", "\r\n", False),
            (b"a\rb\r", "\r", False),
            # One LF and one CRLF: ties break towards CRLF (the more explicit
            # terminator), and the file is flagged as mixed.
            (b"a\nb\r\n", "\r\n", True),
            (b"no endings", "\n", False),
        ],
    )
    def test_detection(self, data, expected, mixed):
        ending, is_mixed = detect_line_ending(data)
        assert ending == expected
        assert is_mixed is mixed

    def test_crlf_round_trip(self, tmp_path: Path):
        target = tmp_path / "crlf.txt"
        target.write_bytes(b"old one\r\nold two\r\n")
        decoded = read_text_file(target)
        assert decoded.line_ending == "\r\n"
        assert decoded.text == "old one\nold two\n"
        assert decoded.encode("new one\nnew two\n") == b"new one\r\nnew two\r\n"

    def test_cr_round_trip(self, tmp_path: Path):
        target = tmp_path / "cr.txt"
        target.write_bytes(b"old\rold\r")
        decoded = read_text_file(target)
        assert decoded.line_ending == "\r"
        assert decoded.encode(decoded.text.replace("old", "new")) == b"new\rnew\r"

    def test_mixed_endings_keep_the_dominant_one(self, tmp_path: Path):
        target = tmp_path / "mixed.txt"
        target.write_bytes(b"a\r\nb\nc\r\n")
        decoded = read_text_file(target)
        assert decoded.newline_mixed is True
        assert decoded.line_ending == "\r\n"

    def test_no_trailing_newline_is_preserved(self, tmp_path: Path):
        target = tmp_path / "noeol.txt"
        target.write_bytes(b"old")
        decoded = read_text_file(target)
        assert decoded.had_trailing_newline is False
        assert decoded.encode("new") == b"new"

    def test_trailing_newline_is_preserved(self, tmp_path: Path):
        target = tmp_path / "eol.txt"
        target.write_bytes(b"old\n")
        decoded = read_text_file(target)
        assert decoded.had_trailing_newline is True
        # A caller that forgets the newline must still get it back.
        assert decoded.encode("new") == b"new\n"


class TestEncodings:
    def test_utf8_default(self, tmp_path: Path):
        target = tmp_path / "utf8.txt"
        target.write_text("café\n", encoding="utf-8")
        decoded = read_text_file(target)
        assert decoded.encoding == "utf-8"
        assert decoded.bom == b""

    def test_utf8_bom_is_preserved(self, tmp_path: Path):
        target = tmp_path / "bom.txt"
        target.write_bytes(codecs.BOM_UTF8 + b"hello\n")
        decoded = read_text_file(target)
        assert decoded.encoding == "utf-8-sig"
        assert decoded.bom == codecs.BOM_UTF8
        assert decoded.text == "hello\n"
        assert decoded.encode("bye\n") == codecs.BOM_UTF8 + b"bye\n"

    def test_utf16_bom_detection(self, tmp_path: Path):
        target = tmp_path / "wide.txt"
        target.write_bytes(codecs.BOM_UTF16_LE + "привет\n".encode("utf-16-le"))
        decoded = read_text_file(target)
        assert "привет" in decoded.text

    def test_explicit_encoding_overrides_detection(self, tmp_path: Path):
        target = tmp_path / "cp1251.txt"
        target.write_bytes("привет".encode("cp1251"))
        decoded = read_text_file(target, encoding="cp1251")
        assert decoded.text == "привет"
        assert decoded.encode(decoded.text) == "привет".encode("cp1251")

    def test_strict_policy_refuses_garbage(self, tmp_path: Path):
        target = tmp_path / "latin.txt"
        target.write_bytes(b"caf\xe9 not utf8")
        with pytest.raises(UndecodableFileError):
            read_text_file(target, errors="strict")

    def test_replace_policy_recovers(self, tmp_path: Path):
        target = tmp_path / "latin.txt"
        target.write_bytes(b"caf\xe9")
        decoded = read_text_file(target, errors="replace")
        assert "caf" in decoded.text

    def test_unknown_encoding_reports_clearly(self):
        with pytest.raises(UndecodableFileError, match="unknown encoding"):
            normalize_codec("definitely-not-a-codec")

    @pytest.mark.parametrize(
        "name,expected",
        [("utf8", "utf-8"), ("UTF-8", "utf-8"), ("latin1", "iso8859-1"), ("", "utf-8")],
    )
    def test_normalization(self, name, expected):
        assert normalize_codec(name) == expected

    def test_max_bytes_guard(self, tmp_path: Path):
        target = tmp_path / "big.txt"
        target.write_bytes(b"x" * 100)
        with pytest.raises(UndecodableFileError, match="read limit"):
            read_text_file(target, max_bytes=10)


class TestDecodeBytes:
    def test_returns_trailing_newline_flag(self):
        _text, _codec, _bom, trailing = decode_bytes(b"line\n")
        assert trailing is True
        _text, _codec, _bom, trailing = decode_bytes(b"line")
        assert trailing is False

    def test_utf16_codec_gets_the_whole_buffer(self):
        payload = "hi".encode("utf-16")
        text, codec, bom, _ = decode_bytes(payload, encoding="utf-16")
        assert text == "hi"
        assert bom == b""
        assert codec.startswith("utf-16")


class TestDecodedFile:
    def test_lines(self):
        decoded = DecodedFile(path=Path("x"), text="a\nb\n")
        assert decoded.lines == ["a", "b", ""]

    def test_encode_strips_an_extra_newline(self):
        decoded = DecodedFile(path=Path("x"), text="a\n", had_trailing_newline=False)
        assert decoded.encode("a\n") == b"a"
