"""DET04 - candidate peeling sequence.

A peel chain moves most of a balance forward while shedding a small amount at each hop.
Without previous-output references the product cannot prove that the forward value is
the same coin, so every link here is labelled inferred continuity: the dominant output
of one transaction is an input of a later one, within a bounded time gap.

Incomplete chains are expected and allowed - the window may simply start or end outside
the imported period.
"""
from __future__ import annotations

from ..graphx.model import CaseData
from .base import (
    CAVEAT_AMOUNT_DISCREPANCY,
    CAVEAT_NOT_INTENT,
    CAVEAT_NO_PREVOUTS,
    RULE_SEVERITY,
    Evidence,
    Finding,
)
from .config import PeelingConfig

DETECTOR_KEY = "peeling_sequence"

#: A side recipient received one branching amount. That is a real association but a
#: much weaker one than carrying the balance forward, so it scores lower.
SIDE_RECIPIENT_SEVERITY = 40.0


def _next_hop(data: CaseData, record, address: str, max_gap_seconds: float) -> int | None:
    """The earliest later transaction that spends ``address``, within the gap."""
    activity = data.addresses.get(address)
    if activity is None:
        return None
    best: int | None = None
    for tx_index in sorted(set(activity.spends)):
        candidate = data.tx(tx_index)
        if candidate.index == record.index or candidate.observed_ts < record.observed_ts:
            continue
        if candidate.observed_ts - record.observed_ts > max_gap_seconds:
            continue
        if best is None or candidate.observed_ts < data.tx(best).observed_ts:
            best = tx_index
    return best


def detect(data: CaseData, config: PeelingConfig, collaborative: set[int]) -> list[Finding]:
    max_gap_seconds = config.max_gap_hours * 3600.0
    chains: list[list[int]] = []
    consumed: set[int] = set()

    for record in data.transactions:
        if record.index in consumed or record.index in collaborative:
            continue
        dominant = record.dominant_output
        if dominant is None or dominant[2] < config.min_dominant_share:
            continue
        if record.output_count < 2:
            continue  # a peel needs something to branch away

        chain = [record.index]
        current, current_address = record, dominant[0]
        while True:
            nxt = _next_hop(data, current, current_address, max_gap_seconds)
            if nxt is None or nxt in consumed or nxt in collaborative:
                break
            candidate = data.tx(nxt)
            nxt_dominant = candidate.dominant_output
            if nxt_dominant is None or nxt_dominant[2] < config.min_dominant_share:
                break
            if candidate.output_count < 2:
                break
            chain.append(nxt)
            current, current_address = candidate, nxt_dominant[0]

        if len(chain) >= config.min_chain_length:
            chains.append(chain)
            consumed.update(chain)

    findings: list[Finding] = []
    for chain in chains:
        records = [data.tx(i) for i in chain]
        gaps = [round((b.observed_ts - a.observed_ts) / 3600.0, 2)
                for a, b in zip(records, records[1:])]
        continuation: list[str] = []
        side_recipients: list[str] = []
        for record in records:
            dominant = record.dominant_output
            assert dominant is not None
            continuation.append(dominant[0])
            side_recipients.extend(a for a, _ in record.outputs if a != dominant[0])

        shares = [record.dominant_output[2] for record in records]  # type: ignore[index]
        quality_affected = any(r.has_amount_discrepancy for r in records)
        peeled = sum(
            sum(a for addr, a in r.outputs if addr != r.dominant_output[0])  # type: ignore[index]
            for r in records)

        caveats = [CAVEAT_NO_PREVOUTS, CAVEAT_NOT_INTENT,
                   "The sequence may be incomplete: hops before the first or after the "
                   "last imported transaction would not be visible in this dataset."]
        if quality_affected:
            caveats.append(CAVEAT_AMOUNT_DISCREPANCY)

        # Every address in the sequence is reported in its own right. An analyst works
        # address by address, and the two positions are not equivalent: the continuation
        # carries the balance forward, while a side recipient received one small amount
        # and may have no further connection to the sequence at all.
        subjects: list[tuple[str, str, float]] = []
        for address in dict.fromkeys(continuation):
            subjects.append((address, "sequence_continuation", RULE_SEVERITY[DETECTOR_KEY]))
        for address in dict.fromkeys(side_recipients):
            if address in set(continuation):
                continue
            subjects.append((address, "sequence_side_recipient", SIDE_RECIPIENT_SEVERITY))

        base_indicators = {
            "hop_count": len(records),
            "continuation_addresses": list(dict.fromkeys(continuation)),
            "side_recipient_addresses": sorted(set(side_recipients)),
            "side_recipient_count": len(set(side_recipients)),
            "dominant_output_shares": [round(s, 4) for s in shares],
            "min_dominant_share_threshold": config.min_dominant_share,
            "hop_gaps_hours": gaps,
            "max_gap_hours_threshold": config.max_gap_hours,
            "total_peeled_off_sats": peeled,
            "entry_value_sats": records[0].total_in_sats,
            "final_continuation_sats": records[-1].dominant_output[1],  # type: ignore[index]
            "txids": [r.txid for r in records],
            "chain_id": f"peel:{records[0].txid[:12]}",
        }
        evidence = [Evidence(
            kind="transaction", txid=r.txid, source_row_id=r.source_row_id,
            row_number=r.row_number,
            detail={
                "hop": position,
                "observed_at": r.observed_at,
                "dominant_output_address": r.dominant_output[0],   # type: ignore[index]
                "dominant_output_sats": r.dominant_output[1],      # type: ignore[index]
                "dominant_share": round(r.dominant_output[2], 4),  # type: ignore[index]
                "side_outputs": [
                    {"address": a, "amount_sats": v}
                    for a, v in r.outputs if a != r.dominant_output[0]  # type: ignore[index]
                ],
                "amount_discrepancy": r.has_amount_discrepancy,
            },
        ) for position, r in enumerate(records, start=1)]

        alternatives = [
            "Staged or instalment payments from a single wallet produce a similar "
            "dominant-output-plus-change shape.",
            "Ordinary wallet change handling creates a large change output on every "
            "spend; that alone is not peeling.",
        ]

        for address, role, severity in subjects:
            role_caveats = list(caveats)
            if role == "sequence_side_recipient":
                role_caveats.append(
                    "This address received one branching amount from the sequence. That "
                    "is a weaker association than carrying the balance forward, and it "
                    "may have no further connection to the sequence.")
            findings.append(Finding(
                detector=DETECTOR_KEY,
                subject_type="address",
                subject_id=address,
                pattern_label="Candidate peeling sequence",
                role_hypothesis=role,
                severity=severity,
                indicators={**base_indicators, "subject_role": role,
                            "subject_position": (
                                list(dict.fromkeys(continuation)).index(address) + 1
                                if role == "sequence_continuation" else None)},
                evidence=evidence,
                period_start=records[0].observed_at,
                period_end=records[-1].observed_at,
                alternatives=alternatives,
                caveats=role_caveats,
                independent_supports=len(records),
                amount_quality_affected=quality_affected,
            ))
    return findings


def continuity_edges(findings: list[Finding], rule_version: str) -> list[dict]:
    """Inferred continuity edges for the graph overlay, one per hop, de-duplicated."""
    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for finding in findings:
        txids = finding.indicators.get("txids", [])
        for source_txid, target_txid in zip(txids, txids[1:]):
            if (source_txid, target_txid) in seen:
                continue
            seen.add((source_txid, target_txid))
            edges.append({
                "source": f"tx:{source_txid}",
                "target": f"tx:{target_txid}",
                "key": f"continuity:{source_txid}:{target_txid}",
                "kind": "inferred_continuity",
                "rule_version": rule_version,
                "supporting_txids": [source_txid, target_txid],
                "note": "Inferred from address reuse and timing; not verified UTXO spending.",
            })
    return edges
