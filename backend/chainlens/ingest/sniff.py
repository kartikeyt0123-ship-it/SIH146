"""Identify an uploaded file by its content, because files may have no extension."""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass


@dataclass(slots=True)
class SniffResult:
    fmt: str                 # "csv" | "json" | "xml" | "unknown"
    encoding: str            # the codec used to decode
    had_bom: bool
    delimiter: str | None    # for csv
    header: list[str]        # detected column / key names
    newline: str             # "crlf" | "lf" | "mixed" | "none"
    note: str = ""


def _decode(data: bytes) -> tuple[str, str, bool]:
    """Decode bytes to text, reporting the codec used and whether a BOM was present."""
    for bom, codec in (
        (b"\xef\xbb\xbf", "utf-8-sig"),
        (b"\xff\xfe\x00\x00", "utf-32-le"),
        (b"\x00\x00\xfe\xff", "utf-32-be"),
        (b"\xff\xfe", "utf-16-le"),
        (b"\xfe\xff", "utf-16-be"),
    ):
        if data.startswith(bom):
            return data.decode(codec), codec, True
    try:
        return data.decode("utf-8"), "utf-8", False
    except UnicodeDecodeError:
        # Last resort so that a mostly-readable file still imports with its issues visible.
        return data.decode("latin-1"), "latin-1", False


def _newline_style(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    if crlf and lf:
        return "mixed"
    if crlf:
        return "crlf"
    if lf:
        return "lf"
    return "none"


def sniff_bytes(data: bytes, filename: str = "") -> SniffResult:
    """Detect format from content. The filename is only used as a tie-breaker hint."""
    text, encoding, had_bom = _decode(data)
    newline = _newline_style(text)
    stripped = text.lstrip("﻿ \t\r\n")

    if stripped.startswith("<"):
        return SniffResult("xml", encoding, had_bom, None, [], newline,
                           "Detected XML by leading '<'.")

    if stripped[:1] in "[{":
        try:
            parsed = json.loads(stripped)
        except ValueError as exc:
            return SniffResult("unknown", encoding, had_bom, None, [], newline,
                               f"Looks like JSON but did not parse: {exc}")
        records = parsed if isinstance(parsed, list) else parsed.get("transactions", [])
        header = sorted(records[0].keys()) if records and isinstance(records[0], dict) else []
        return SniffResult("json", encoding, had_bom, None, header, newline,
                           "Detected JSON by leading bracket.")

    sample = stripped[:64 * 1024]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    # A semicolon delimiter would collide with the array separator; prefer comma unless
    # the comma reading produces a single column.
    if delimiter == ";":
        first = sample.splitlines()[0] if sample.splitlines() else ""
        delimiter = "," if first.count(",") >= 1 else ";"

    reader = csv.reader(io.StringIO(sample), delimiter=delimiter)
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        header = []
    fmt = "csv" if header else "unknown"
    return SniffResult(fmt, encoding, had_bom, delimiter, header, newline,
                       f"Detected CSV with delimiter {delimiter!r}." if fmt == "csv"
                       else "Could not identify the file format.")
