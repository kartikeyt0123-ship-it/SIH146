"""Format adapters. Each yields (row_number, mapping) pairs in file order.

Row numbers are 1-based data rows (a CSV header is row 0) so that every finding can be
traced back to a line the analyst can locate in the original file.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Iterator

from defusedxml import ElementTree as DefusedET

from .sniff import SniffResult

RawRow = tuple[int, dict[str, Any]]

#: XML/JSON documents may nest the array fields as repeated child elements; these are the
#: element names understood for that shape.
_ARRAY_CONTAINERS = {
    "input_addresses": "address",
    "output_addresses": "address",
    "input_amounts": "amount",
    "output_amounts": "amount",
}


class AdapterError(RuntimeError):
    """Raised when a whole file cannot be read (as opposed to one bad row)."""


def _decode(data: bytes, sniff: SniffResult) -> str:
    return data.decode(sniff.encoding, errors="replace")


def iter_csv(data: bytes, sniff: SniffResult) -> Iterator[RawRow]:
    text = _decode(data, sniff)
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=sniff.delimiter or ",")
    if reader.fieldnames is None:
        raise AdapterError("The CSV file has no header row.")
    for index, row in enumerate(reader, start=1):
        # csv.DictReader stores surplus cells under None; keep them visible as a field so
        # the row is reported rather than silently truncated.
        extra = row.pop(None, None)
        clean = {(k.strip() if k else k): v for k, v in row.items()}
        if extra:
            clean["__extra_cells__"] = ";".join(str(x) for x in extra)
        yield index, clean


def _flatten(value: Any, key: str) -> Any:
    """Render a nested list as the semicolon-separated string the contract expects."""
    if isinstance(value, list):
        return ";".join(str(v) for v in value)
    if isinstance(value, dict) and key in _ARRAY_CONTAINERS:
        inner = value.get(_ARRAY_CONTAINERS[key])
        if isinstance(inner, list):
            return ";".join(str(v) for v in inner)
        return "" if inner is None else str(inner)
    return value


def iter_json(data: bytes, sniff: SniffResult) -> Iterator[RawRow]:
    text = _decode(data, sniff)
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise AdapterError(f"The JSON file did not parse: {exc}") from exc
    if isinstance(parsed, dict):
        records = parsed.get("transactions")
        if records is None:
            raise AdapterError("JSON object has no 'transactions' array.")
    else:
        records = parsed
    if not isinstance(records, list):
        raise AdapterError("Expected a JSON array of transaction objects.")
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            yield index, {"__unparseable__": json.dumps(record)}
            continue
        yield index, {k: _flatten(v, k) for k, v in record.items()}


def iter_xml(data: bytes, sniff: SniffResult) -> Iterator[RawRow]:
    """Parse XML with defusedxml, which disables external entity resolution."""
    try:
        root = DefusedET.fromstring(data)
    except Exception as exc:  # defusedxml raises several distinct types
        raise AdapterError(f"The XML file did not parse: {exc}") from exc

    records = root.findall("transaction") or list(root)
    for index, node in enumerate(records, start=1):
        row: dict[str, Any] = {k: v for k, v in node.attrib.items()}
        for child in node:
            tag = child.tag
            grandchildren = list(child)
            if grandchildren:
                row[tag] = ";".join((gc.text or "").strip() for gc in grandchildren)
            else:
                row[tag] = (child.text or "").strip()
        yield index, row


def iter_rows(data: bytes, sniff: SniffResult) -> Iterator[RawRow]:
    if sniff.fmt == "csv":
        yield from iter_csv(data, sniff)
    elif sniff.fmt == "json":
        yield from iter_json(data, sniff)
    elif sniff.fmt == "xml":
        yield from iter_xml(data, sniff)
    else:
        raise AdapterError(sniff.note or "Unrecognised file format.")
