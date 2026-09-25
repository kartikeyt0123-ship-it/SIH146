"""Turn one raw row into a canonical, label-free transaction record.

Two rules govern this module:

1. Nothing is silently repaired. A supplied fee stays exactly as supplied; the residual
   is calculated independently and reported.
2. Evaluation labels are removed here, at the boundary, so that no downstream caller can
   reach them even by accident.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .contract import (
    DEFAULT_FEE_TOLERANCE_SATS,
    DEMOTING_SEVERITIES,
    FORBIDDEN_LABEL_FIELDS,
    ISSUE_TEXT,
    KNOWN_SCRIPT_TYPES,
    REQUIRED_FIELDS,
    IssueCode,
    NormalizedTransaction,
    RowIssue,
    RowOutcome,
    TxEntry,
    ValidationMode,
)

SATOSHIS_PER_BTC = Decimal(10) ** 8
ARRAY_SEPARATOR = ";"

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(_B58_ALPHABET)}
_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def strip_label_fields(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Remove evaluation label columns. Returns the sanitised mapping and what was removed."""
    removed = [k for k in raw if k.strip().lower() in FORBIDDEN_LABEL_FIELDS]
    if not removed:
        return dict(raw), []
    return {k: v for k, v in raw.items() if k not in removed}, removed


def btc_to_sats(text: str) -> int:
    """Exact BTC -> integer satoshi conversion. Raises ValueError on anything inexact."""
    value = Decimal(text.strip())
    scaled = value * SATOSHIS_PER_BTC
    if scaled != scaled.to_integral_value():
        raise ValueError(f"amount {text!r} has sub-satoshi precision")
    return int(scaled)


def sats_to_btc_str(sats: int) -> str:
    """Render satoshis as a plain BTC decimal string (no exponent notation)."""
    return f"{Decimal(sats) / SATOSHIS_PER_BTC:.8f}"


def parse_timestamp(text: str) -> tuple[str, float]:
    """Parse a timestamp as a timezone-aware UTC instant.

    A value without an explicit offset is read as UTC, which the import summary states.
    """
    cleaned = text.strip()
    candidate = cleaned[:-1] + "+00:00" if cleaned.endswith(("Z", "z")) else cleaned
    dt = datetime.fromisoformat(candidate)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ"), dt.timestamp()


def _b58check_ok(address: str) -> bool:
    try:
        num = 0
        for ch in address:
            num = num * 58 + _B58_INDEX[ch]
    except KeyError:
        return False
    pad = len(address) - len(address.lstrip("1"))
    body = num.to_bytes((num.bit_length() + 7) // 8, "big")
    payload = b"\x00" * pad + body
    if len(payload) < 5:
        return False
    data, checksum = payload[:-4], payload[-4:]
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()[:4] == checksum


def _bech32_ok(address: str) -> bool:
    lowered = address.lower()
    if lowered != address and address.upper() != address:
        return False
    pos = lowered.rfind("1")
    if pos < 1 or pos + 7 > len(lowered):
        return False
    hrp, data_part = lowered[:pos], lowered[pos + 1:]
    if any(c not in _BECH32_CHARSET for c in data_part):
        return False
    data = [_BECH32_CHARSET.index(c) for c in data_part]
    expanded = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp] + data

    chk = 1
    for value in expanded:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ value
        for i, gen in enumerate((0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)):
            if (top >> i) & 1:
                chk ^= gen
    return chk in (1, 0x2BC830A3)  # bech32 and bech32m


def address_checksum_valid(address: str) -> bool:
    """True when the address verifies as Base58Check or Bech32/Bech32m.

    Synthetic sample addresses fail this. That is recorded as an informational issue,
    never a rejection:
    in compatibility mode an address is an opaque identifier.
    """
    if not address:
        return False
    if address[0] in "13" and all(c in _B58_ALPHABET for c in address):
        return _b58check_ok(address)
    if "1" in address.lower() and address.lower()[:2] in ("bc", "tb") or address.lower().startswith("bcrt"):
        return _bech32_ok(address)
    return False


def _split_array(value: str) -> list[str]:
    return [part for part in value.split(ARRAY_SEPARATOR)] if value != "" else []


def payload_hash(tx: NormalizedTransaction) -> str:
    """Stable hash of the transaction payload, ignoring network observation fields.

    Two rows with the same TXID and the same payload are the same transaction seen
    twice; the same TXID with a different payload is a conflict.
    """
    body = {
        "txid": tx.txid,
        "fee_sats": tx.fee_sats,
        "script_type": tx.script_type,
        "entries": [[e.side, e.position, e.address, e.amount_sats] for e in tx.entries],
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def normalize_row(
    raw: dict[str, Any],
    row_number: int,
    mode: ValidationMode = ValidationMode.COMPATIBILITY,
    fee_tolerance_sats: int = DEFAULT_FEE_TOLERANCE_SATS,
) -> RowOutcome:
    """Validate and canonicalise a single row.

    Returns a :class:`RowOutcome` whose ``status`` is one of ``accepted`` (no issues),
    ``warning`` (usable, issues recorded) or ``quarantined`` (not analysable).
    """
    original = {str(k): ("" if v is None else str(v)) for k, v in raw.items()}
    sanitised, removed_labels = strip_label_fields(original)
    issues: list[RowIssue] = []

    def add(code: IssueCode, severity: str, field: str | None = None, extra: str = "") -> None:
        issues.append(RowIssue(code, severity, (ISSUE_TEXT[code] + (" " + extra if extra else "")).strip(), field))

    def fail() -> RowOutcome:
        return RowOutcome(row_number, "quarantined", None, issues, original, removed_labels)

    # Label removal is reported once at dataset level by the importer. It is a property
    # of the upload, not a defect in this row, so it is not recorded as a row issue.

    # --- required fields -----------------------------------------------------------
    missing = [f for f in REQUIRED_FIELDS if not sanitised.get(f, "").strip()]
    if missing:
        for f in missing:
            add(IssueCode.SCHEMA_MISSING_FIELD, "error", f, f"Field: {f}.")
        return fail()

    # --- identifier ----------------------------------------------------------------
    txid = sanitised["txid"].strip()
    if len(txid) != 64 or any(c not in "0123456789abcdefABCDEF" for c in txid):
        add(IssueCode.TXID_FORMAT_UNVERIFIED, "error" if mode is ValidationMode.STRICT else "warning", "txid")
        if mode is ValidationMode.STRICT:
            return fail()

    # --- timestamp -----------------------------------------------------------------
    try:
        observed_at, observed_ts = parse_timestamp(sanitised["timestamp"])
    except (ValueError, TypeError):
        add(IssueCode.TIMESTAMP_INVALID, "error", "timestamp",
            f"Value: {sanitised['timestamp']!r}.")
        return fail()

    # --- arrays --------------------------------------------------------------------
    entries: list[TxEntry] = []
    totals = {"input": 0, "output": 0}
    checksum_failures = 0
    for side, addr_field, amt_field in (
        ("input", "input_addresses", "input_amounts"),
        ("output", "output_addresses", "output_amounts"),
    ):
        addresses = _split_array(sanitised[addr_field])
        amounts = _split_array(sanitised[amt_field])
        if not addresses or not amounts:
            add(IssueCode.ARRAY_EMPTY, "error", addr_field)
            return fail()
        if len(addresses) != len(amounts):
            add(IssueCode.ARRAY_LENGTH_MISMATCH, "error", addr_field,
                f"{addr_field}={len(addresses)} vs {amt_field}={len(amounts)}.")
            return fail()
        if any(not a.strip() for a in addresses) or any(not a.strip() for a in amounts):
            add(IssueCode.ARRAY_EMPTY_ELEMENT, "error", addr_field)
            return fail()

        seen: set[str] = set()
        for position, (address, amount_text) in enumerate(zip(addresses, amounts)):
            address = address.strip()
            try:
                amount_sats = btc_to_sats(amount_text)
            except (InvalidOperation, ValueError, ArithmeticError):
                add(IssueCode.AMOUNT_INVALID, "error", amt_field,
                    f"Position {position}, value {amount_text!r}.")
                return fail()
            if amount_sats < 0:
                add(IssueCode.AMOUNT_NEGATIVE, "error", amt_field, f"Position {position}.")
                return fail()
            if address in seen:
                add(IssueCode.ADDRESS_REPEATED_IN_ROW, "warning", addr_field,
                    f"Address repeats on the {side} side; both occurrences are preserved.")
            seen.add(address)
            if not address_checksum_valid(address):
                checksum_failures += 1
            entries.append(TxEntry(side, position, address, amount_sats))
            totals[side] += amount_sats

    if checksum_failures:
        add(IssueCode.ADDRESS_CHECKSUM_UNVERIFIED, "info", "addresses",
            f"{checksum_failures} of {len(entries)} address occurrences did not verify.")

    # --- fee and conservation residual ---------------------------------------------
    fee_text = sanitised.get("fee", "").strip()
    fee_sats: int | None = None
    if not fee_text:
        add(IssueCode.FEE_MISSING, "warning", "fee")
    else:
        try:
            fee_sats = btc_to_sats(fee_text)
        except (InvalidOperation, ValueError, ArithmeticError):
            add(IssueCode.FEE_INVALID, "error", "fee", f"Value: {fee_text!r}.")
            if mode is ValidationMode.STRICT:
                return fail()
        else:
            if fee_sats < 0:
                add(IssueCode.FEE_NEGATIVE, "error", "fee")
                if mode is ValidationMode.STRICT:
                    return fail()

    residual = totals["input"] - totals["output"] - (fee_sats or 0)
    if fee_sats is not None and residual != 0:
        if abs(residual) <= fee_tolerance_sats:
            add(IssueCode.FEE_RESIDUAL_WITHIN_TOLERANCE, "warning", "fee",
                f"Residual {residual} sat.")
        else:
            add(IssueCode.FEE_RESIDUAL_ABOVE_TOLERANCE,
                "error" if mode is ValidationMode.STRICT else "warning", "fee",
                f"Residual {residual} sat.")
            if mode is ValidationMode.STRICT:
                return fail()

    # --- network observation fields -------------------------------------------------
    endpoints: dict[str, Any] = {}
    for ip_field in ("src_ip", "dst_ip"):
        value = sanitised.get(ip_field, "").strip()
        if not value:
            endpoints[ip_field] = None
            continue
        try:
            endpoints[ip_field] = str(ipaddress.ip_address(value))
        except ValueError:
            add(IssueCode.IP_INVALID, "warning", ip_field, f"Value: {value!r}.")
            endpoints[ip_field] = None
    for port_field in ("src_port", "dst_port"):
        value = sanitised.get(port_field, "").strip()
        if not value:
            endpoints[port_field] = None
            continue
        try:
            port = int(value)
        except ValueError:
            add(IssueCode.PORT_INVALID, "warning", port_field, f"Value: {value!r}.")
            endpoints[port_field] = None
            continue
        if not 0 <= port <= 65535:
            add(IssueCode.PORT_INVALID, "warning", port_field, f"Value: {port}.")
            endpoints[port_field] = None
        else:
            endpoints[port_field] = port

    script_type = sanitised.get("script_type", "").strip() or None
    if script_type and script_type not in KNOWN_SCRIPT_TYPES:
        add(IssueCode.SCRIPT_TYPE_UNKNOWN, "warning", "script_type", f"Value: {script_type!r}.")

    tx = NormalizedTransaction(
        txid=txid,
        observed_at=observed_at,
        observed_ts=observed_ts,
        entries=entries,
        fee_sats=fee_sats,
        total_in_sats=totals["input"],
        total_out_sats=totals["output"],
        residual_sats=residual,
        script_type=script_type,
        src_ip=endpoints["src_ip"],
        src_port=endpoints["src_port"],
        dst_ip=endpoints["dst_ip"],
        dst_port=endpoints["dst_port"],
    )
    demoting = any(i.severity in DEMOTING_SEVERITIES for i in issues)
    return RowOutcome(row_number, "warning" if demoting else "accepted",
                      tx, issues, original, removed_labels)
