"""The input contract: field names, issue codes and the canonical record shape.

This module is deliberately free of I/O so that the contract can be read, tested and
documented on its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: Fields the transaction contract requires in every record.
REQUIRED_FIELDS: tuple[str, ...] = (
    "timestamp",
    "txid",
    "input_addresses",
    "output_addresses",
    "input_amounts",
    "output_amounts",
)

#: Fields that are part of the contract but may be absent or blank.
OPTIONAL_FIELDS: tuple[str, ...] = (
    "src_ip",
    "src_port",
    "dst_ip",
    "dst_port",
    "fee",
    "script_type",
)

#: Any column matching one of these names is an evaluation label, never an input to
#: analysis. It is stripped at the ingestion boundary before a canonical record exists.
FORBIDDEN_LABEL_FIELDS: frozenset[str] = frozenset(
    {
        "is_planted_suspicious",
        "is_suspicious",
        "pattern_type",
        "ground_truth",
        "label",
        "wallet_id_label",
        "planted",
    }
)

#: Script types the product recognises. Unfamiliar values are preserved, not rejected.
KNOWN_SCRIPT_TYPES: frozenset[str] = frozenset({"P2PKH", "P2SH", "P2WPKH", "P2WSH", "P2TR", "P2MS"})

#: One satoshi expressed in BTC; used for the default conservation tolerance.
DEFAULT_FEE_TOLERANCE_SATS = 1


class ValidationMode(str, Enum):
    """How strictly a record must match the contract to become analysable."""

    COMPATIBILITY = "compatibility"
    STRICT = "strict"


class IssueCode(str, Enum):
    """Stable machine-readable validation codes surfaced to the analyst and the API."""

    SCHEMA_MISSING_FIELD = "SCHEMA_MISSING_FIELD"
    ARRAY_LENGTH_MISMATCH = "ARRAY_LENGTH_MISMATCH"
    ARRAY_EMPTY = "ARRAY_EMPTY"
    ARRAY_EMPTY_ELEMENT = "ARRAY_EMPTY_ELEMENT"
    AMOUNT_INVALID = "AMOUNT_INVALID"
    AMOUNT_NEGATIVE = "AMOUNT_NEGATIVE"
    FEE_INVALID = "FEE_INVALID"
    FEE_NEGATIVE = "FEE_NEGATIVE"
    FEE_MISSING = "FEE_MISSING"
    FEE_RESIDUAL_WITHIN_TOLERANCE = "FEE_RESIDUAL_WITHIN_TOLERANCE"
    FEE_RESIDUAL_ABOVE_TOLERANCE = "FEE_RESIDUAL_ABOVE_TOLERANCE"
    TIMESTAMP_INVALID = "TIMESTAMP_INVALID"
    TXID_MISSING = "TXID_MISSING"
    TXID_FORMAT_UNVERIFIED = "TXID_FORMAT_UNVERIFIED"
    TXID_CONFLICT = "TXID_CONFLICT"
    DUPLICATE_OBSERVATION = "DUPLICATE_OBSERVATION"
    IP_INVALID = "IP_INVALID"
    PORT_INVALID = "PORT_INVALID"
    ADDRESS_CHECKSUM_UNVERIFIED = "ADDRESS_CHECKSUM_UNVERIFIED"
    ADDRESS_REPEATED_IN_ROW = "ADDRESS_REPEATED_IN_ROW"
    SCRIPT_TYPE_UNKNOWN = "SCRIPT_TYPE_UNKNOWN"
    LABEL_COLUMN_REMOVED = "LABEL_COLUMN_REMOVED"
    ROW_UNPARSEABLE = "ROW_UNPARSEABLE"


#: Human-readable text for each code, shown in the import summary and quality panel.
ISSUE_TEXT: dict[IssueCode, str] = {
    IssueCode.SCHEMA_MISSING_FIELD: "A required contract field is absent or blank.",
    IssueCode.ARRAY_LENGTH_MISMATCH: "Address and amount arrays have different lengths.",
    IssueCode.ARRAY_EMPTY: "A required address/amount array is empty.",
    IssueCode.ARRAY_EMPTY_ELEMENT: "An array element is empty after splitting on ';'.",
    IssueCode.AMOUNT_INVALID: "An amount could not be read as a decimal BTC value.",
    IssueCode.AMOUNT_NEGATIVE: "An amount is negative.",
    IssueCode.FEE_INVALID: "The fee could not be read as a decimal BTC value.",
    IssueCode.FEE_NEGATIVE: "The fee is negative.",
    IssueCode.FEE_MISSING: "No fee was supplied; the residual cannot be attributed.",
    IssueCode.FEE_RESIDUAL_WITHIN_TOLERANCE: (
        "inputs - outputs does not equal the supplied fee, but the difference is within "
        "the declared tolerance. Original values are preserved."
    ),
    IssueCode.FEE_RESIDUAL_ABOVE_TOLERANCE: (
        "inputs - outputs does not equal the supplied fee by more than the declared "
        "tolerance. This is a data-quality finding, not a behavioural detection."
    ),
    IssueCode.TIMESTAMP_INVALID: "The timestamp could not be parsed as a UTC instant.",
    IssueCode.TXID_MISSING: "No transaction identifier was supplied.",
    IssueCode.TXID_FORMAT_UNVERIFIED: (
        "The identifier is not 64 hexadecimal characters. Accepted as an opaque "
        "identifier in compatibility mode."
    ),
    IssueCode.TXID_CONFLICT: (
        "Another row in this case carries the same TXID with a different payload. "
        "Both are quarantined rather than one overwriting the other."
    ),
    IssueCode.DUPLICATE_OBSERVATION: (
        "The same TXID and payload was seen again; recorded as an additional network "
        "observation rather than a second transaction."
    ),
    IssueCode.IP_INVALID: "An endpoint address is not a valid IPv4/IPv6 address.",
    IssueCode.PORT_INVALID: "A port is outside 0-65535.",
    IssueCode.ADDRESS_CHECKSUM_UNVERIFIED: (
        "One or more addresses failed Base58Check/Bech32 verification. They are kept as "
        "opaque identifiers; no real-chain address validity is claimed."
    ),
    IssueCode.ADDRESS_REPEATED_IN_ROW: "An address occurs more than once on the same side.",
    IssueCode.SCRIPT_TYPE_UNKNOWN: "script_type is not a recognised category; preserved as supplied.",
    IssueCode.LABEL_COLUMN_REMOVED: "An evaluation label column was removed before analysis.",
    IssueCode.ROW_UNPARSEABLE: "The row could not be read at all.",
}


#: Severities. ``info`` is recorded and traceable but does not demote a row from
#: "accepted" - it describes a property of the whole synthetic dataset (for example that
#: its addresses are not real-chain addresses) rather than a defect in that row.
SEVERITIES = ("info", "warning", "error")
DEMOTING_SEVERITIES = ("warning", "error")


@dataclass(slots=True)
class RowIssue:
    code: IssueCode
    severity: str  # "info" | "warning" | "error"
    message: str
    field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "severity": self.severity,
            "message": self.message,
            "field": self.field,
        }


@dataclass(slots=True)
class TxEntry:
    """One input or output, with its original array position preserved."""

    side: str  # "input" | "output"
    position: int
    address: str
    amount_sats: int


@dataclass(slots=True)
class NormalizedTransaction:
    """A canonical, label-free transaction record."""

    txid: str
    observed_at: str
    observed_ts: float
    entries: list[TxEntry]
    fee_sats: int | None
    total_in_sats: int
    total_out_sats: int
    residual_sats: int
    script_type: str | None
    src_ip: str | None
    src_port: int | None
    dst_ip: str | None
    dst_port: int | None

    @property
    def inputs(self) -> list[TxEntry]:
        return [e for e in self.entries if e.side == "input"]

    @property
    def outputs(self) -> list[TxEntry]:
        return [e for e in self.entries if e.side == "output"]


@dataclass(slots=True)
class RowOutcome:
    """The result of normalising one source row."""

    row_number: int
    status: str  # "accepted" | "warning" | "quarantined"
    transaction: NormalizedTransaction | None
    issues: list[RowIssue] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    removed_label_fields: list[str] = field(default_factory=list)
