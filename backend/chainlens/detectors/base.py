"""Shared detector vocabulary: findings, evidence and the role/severity contract."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Default rule severities, on the same 0-100 scale as review priority.
#:
#: These are review-policy choices about what an analyst should look at first. They are
#: not learned probabilities and are not statements about the likelihood of wrongdoing.
#: When several rules fire for one subject the maximum is taken, never the sum.
RULE_SEVERITY: dict[str, float] = {
    "collection_pattern": 75.0,
    "peeling_sequence": 75.0,
    "common_control": 40.0,
    "coinjoin_like": 25.0,
    "payment_participant": 20.0,
    "high_activity_context": 0.0,
    "shared_ip_observation": 0.0,
    "statistical_anomaly": 0.0,
    "no_supported_pattern": 0.0,
}

#: Analyst-facing role hypotheses. A role is a position in an observed structure, not an
#: accusation and not an identity.
ROLES = (
    "unknown_role",
    "collection_point",
    "potential_payment_participant",
    "sequence_continuation",
    "sequence_side_recipient",
    "collaborative_transaction_participant",
    "possible_common_control_member",
    "high_activity_service_like",
)


@dataclass(slots=True)
class Evidence:
    """A pointer from a finding back to a record the analyst can open."""

    kind: str                       # "transaction" | "observation" | "derived"
    txid: str | None = None
    source_row_id: int | None = None
    row_number: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "txid": self.txid,
            "source_row_id": self.source_row_id, "row_number": self.row_number,
            "detail": self.detail,
        }


@dataclass(slots=True)
class Finding:
    """One detector result about one subject.

    A finding carries only computed values. Explanations are rendered later from these
    values by a deterministic template, so no wording can assert anything the detector
    did not actually measure.
    """

    detector: str
    subject_type: str               # "address" | "transaction"
    subject_id: str
    pattern_label: str              # wording shown to the analyst
    role_hypothesis: str = "unknown_role"
    severity: float = 0.0
    indicators: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    period_start: str | None = None
    period_end: str | None = None
    alternatives: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    #: Count of independent supporting observations, used for evidence strength.
    independent_supports: int = 1
    #: True when any supporting record has an unresolved amount discrepancy.
    amount_quality_affected: bool = False
    #: Set by a safeguard that deliberately holds priority down, with the reason.
    suppression: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "pattern_label": self.pattern_label,
            "role_hypothesis": self.role_hypothesis,
            "severity": self.severity,
            "indicators": self.indicators,
            "evidence": [e.to_dict() for e in self.evidence],
            "period_start": self.period_start,
            "period_end": self.period_end,
            "alternatives": self.alternatives,
            "caveats": self.caveats,
            "independent_supports": self.independent_supports,
            "amount_quality_affected": self.amount_quality_affected,
            "suppression": self.suppression,
        }


#: Caveats that apply to the whole contract and are attached wherever relevant, so the
#: analyst is never shown an inference without its limitation.
CAVEAT_NO_PREVOUTS = (
    "The supplied records carry no previous-output references, so continuity between "
    "transactions is inferred from address reuse and timing, not verified UTXO spending."
)
CAVEAT_NO_OWNERSHIP = (
    "Addresses appearing together does not establish that one person or entity controls "
    "them. No identity is asserted."
)
CAVEAT_IP_NOT_ORIGIN = (
    "A network observation records where a record was seen, not where a transaction "
    "originated and not who controls an address."
)
CAVEAT_AMOUNT_DISCREPANCY = (
    "At least one supporting record's amounts do not reconcile with its supplied fee. "
    "Structural findings still stand; any amount-derived figure here is provisional."
)
CAVEAT_NOT_INTENT = (
    "This is a structural observation. It does not establish intent, criminality or the "
    "identity of any participant."
)
