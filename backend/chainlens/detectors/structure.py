"""DET01 - CoinJoin-like structure.

A CoinJoin is recognised by its shape: several inputs, several outputs, and a group of
outputs sharing one denomination. The detector reports that shape and nothing more. It
does not infer laundering, and the transactions it tags are excluded from common-input
ownership merging elsewhere, because a collaborative transaction is exactly the case
where "shared inputs means shared owner" is wrong.
"""
from __future__ import annotations

from ..graphx.model import CaseData, TxRecord
from .base import (
    CAVEAT_AMOUNT_DISCREPANCY,
    CAVEAT_NOT_INTENT,
    RULE_SEVERITY,
    Evidence,
    Finding,
)
from .config import CoinJoinConfig

DETECTOR_KEY = "coinjoin_like"


def equal_value_groups(amounts: list[int], band_sats: int) -> list[list[int]]:
    """Group amounts that fall within ``band_sats`` of each other, largest group first.

    Grouping is greedy over sorted values, which is sufficient for the narrow bands used
    to absorb rounding (a band of one satoshi) and is deterministic.
    """
    groups: list[list[int]] = []
    for value in sorted(amounts):
        if groups and value - groups[-1][0] <= band_sats:
            groups[-1].append(value)
        else:
            groups.append([value])
    return sorted(groups, key=len, reverse=True)


def is_coinjoin_like(record: TxRecord, config: CoinJoinConfig) -> tuple[bool, dict]:
    """Return whether the transaction has the structure, plus the measured values."""
    out_amounts = [amount for _, amount in record.outputs]
    groups = equal_value_groups(out_amounts, config.equal_amount_band_sats)
    largest = groups[0] if groups else []
    indicators = {
        "input_count": record.input_count,
        "output_count": record.output_count,
        "equal_output_count": len(largest),
        "equal_output_share": round(len(largest) / record.output_count, 4) if record.output_count else 0.0,
        "denomination_sats": largest[0] if largest else None,
        "denomination_group_count": sum(1 for g in groups if len(g) >= config.min_equal_outputs),
        "distinct_output_values": len(set(out_amounts)),
        "equal_amount_band_sats": config.equal_amount_band_sats,
    }
    matched = (
        record.input_count >= config.min_inputs
        and record.output_count >= config.min_outputs
        and len(largest) >= config.min_equal_outputs
    )
    return matched, indicators


def detect(data: CaseData, config: CoinJoinConfig) -> tuple[list[Finding], set[int]]:
    """Find CoinJoin-like transactions.

    Returns the findings and the set of transaction indices that later detectors must
    treat as possibly collaborative.
    """
    findings: list[Finding] = []
    collaborative: set[int] = set()

    for record in data.transactions:
        matched, indicators = is_coinjoin_like(record, config)
        if not matched:
            continue
        collaborative.add(record.index)

        caveats = [CAVEAT_NOT_INTENT,
                   "Common-input ownership merging is suppressed for this transaction: "
                   "the inputs of a collaborative transaction generally belong to "
                   "different parties."]
        if record.has_amount_discrepancy:
            caveats.append(CAVEAT_AMOUNT_DISCREPANCY)

        findings.append(Finding(
            detector=DETECTOR_KEY,
            subject_type="transaction",
            subject_id=record.txid,
            pattern_label="CoinJoin-like structure",
            role_hypothesis="collaborative_transaction_participant",
            severity=RULE_SEVERITY[DETECTOR_KEY],
            indicators=indicators,
            evidence=[Evidence(
                kind="transaction", txid=record.txid,
                source_row_id=record.source_row_id, row_number=record.row_number,
                detail={
                    "input_count": record.input_count,
                    "output_count": record.output_count,
                    "input_addresses": record.input_addresses,
                    "output_addresses": record.output_addresses,
                    "output_amounts_sats": [a for _, a in record.outputs],
                    "observed_at": record.observed_at,
                },
            )],
            period_start=record.observed_at,
            period_end=record.observed_at,
            alternatives=[
                "A batched payout from one service can also produce many equal-value "
                "outputs without any mixing taking place.",
                "A collaborative transaction is a legitimate privacy technique in many "
                "jurisdictions; participation alone is not wrongdoing.",
            ],
            caveats=caveats,
            independent_supports=1,
            amount_quality_affected=record.has_amount_discrepancy,
        ))
    return findings, collaborative
