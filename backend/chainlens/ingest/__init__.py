"""Ingestion: file sniffing, canonical normalisation, validation policies."""

from .contract import (  # noqa: F401
    FORBIDDEN_LABEL_FIELDS,
    REQUIRED_FIELDS,
    IssueCode,
    NormalizedTransaction,
    RowIssue,
    RowOutcome,
    ValidationMode,
)
from .sniff import SniffResult, sniff_bytes  # noqa: F401
from .normalize import normalize_row  # noqa: F401
from .importer import import_dataset  # noqa: F401
