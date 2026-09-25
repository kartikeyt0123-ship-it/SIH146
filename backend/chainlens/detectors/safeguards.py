"""DET06 - shared IP safeguard, DET07 - high-volume safeguard, DET08 - general anomaly.

These three exist to stop the system reaching a confident conclusion it has no basis
for. They produce context, and in two cases they actively hold priority down.
"""
from __future__ import annotations

import statistics
from collections import defaultdict

from ..graphx.model import CaseData
from .base import (
    CAVEAT_IP_NOT_ORIGIN,
    CAVEAT_NOT_INTENT,
    CAVEAT_NO_OWNERSHIP,
    RULE_SEVERITY,
    Evidence,
    Finding,
)
from .config import AnomalyConfig, HighVolumeConfig, SharedIpConfig

SHARED_IP_KEY = "shared_ip_observation"
HIGH_VOLUME_KEY = "high_activity_context"
ANOMALY_KEY = "statistical_anomaly"


def detect_shared_ip(data: CaseData, config: SharedIpConfig) -> list[Finding]:
    """Report addresses seen at the same endpoint - as an observation, never a merge.

    This detector deliberately produces zero severity and creates no ownership edge. It
    exists so the analyst can see the association and be told what it does not mean.
    """
    findings: list[Finding] = []

    for ip, tx_indices in sorted(data.ip_to_tx.items()):
        if len(tx_indices) < config.min_observations:
            continue

        # Count how often each address is seen at this endpoint. Every transaction
        # contributes several addresses, so a one-off counterparty says nothing; what
        # matters is which addresses keep appearing here.
        appearances: dict[str, int] = defaultdict(int)
        for index in tx_indices:
            record = data.tx(index)
            for address in set(record.input_addresses) | set(record.output_addresses):
                appearances[address] += 1
        recurring = sorted(a for a, n in appearances.items()
                           if n >= config.min_observations_per_address)
        if len(recurring) < config.min_recurring_addresses:
            continue

        records = [data.tx(i) for i in tx_indices]
        looks_like_infrastructure = len(recurring) >= config.infrastructure_address_count
        indicators = {
            "endpoint": ip,
            "distinct_addresses_observed": len(recurring),
            "addresses_seen_at_endpoint": len(appearances),
            "observation_count": len(tx_indices),
            "min_observations_per_address": config.min_observations_per_address,
            "addresses": recurring[:50],
            "addresses_truncated": len(recurring) > 50,
            "looks_like_shared_infrastructure": looks_like_infrastructure,
            "creates_ownership_edge": False,
        }
        findings.append(Finding(
            detector=SHARED_IP_KEY,
            subject_type="address",
            subject_id=recurring[0],
            pattern_label="Shared network observation",
            role_hypothesis="unknown_role",
            severity=RULE_SEVERITY[SHARED_IP_KEY],
            indicators=indicators,
            evidence=[Evidence(
                kind="observation", txid=r.txid, source_row_id=r.source_row_id,
                row_number=r.row_number,
                detail={"observed_at": r.observed_at, "src_ip": ip},
            ) for r in records[:50]],
            period_start=records[0].observed_at,
            period_end=records[-1].observed_at,
            alternatives=[
                "A NAT gateway, VPN exit, mobile carrier or public network puts many "
                "unrelated users behind one address.",
                "A node relaying other people's transactions is observed as the source "
                "of traffic it did not originate.",
            ],
            caveats=[
                CAVEAT_IP_NOT_ORIGIN,
                "These addresses have NOT been grouped and no review priority has been "
                "transferred between them on the basis of this endpoint.",
                CAVEAT_NO_OWNERSHIP,
            ],
            independent_supports=1,
            suppression="Network association only; contributes no severity by design.",
        ))
    return findings


def detect_high_volume(data: CaseData, config: HighVolumeConfig) -> list[Finding]:
    """Flag high-activity addresses as needing context rather than as leads.

    Volume is compared against this case's own population, so the rule does not depend
    on a hardcoded idea of what "busy" means.
    """
    counts = {address: len(activity.tx_indices) for address, activity in data.addresses.items()}
    if not counts:
        return []
    ordered = sorted(counts.values())
    cut_index = min(len(ordered) - 1,
                    int(round(config.activity_percentile / 100.0 * (len(ordered) - 1))))
    percentile_cut = ordered[cut_index]

    findings: list[Finding] = []
    for address, count in sorted(counts.items()):
        if count < max(percentile_cut, config.min_transactions):
            continue
        activity = data.addresses[address]

        senders: set[str] = set()
        receivers: set[str] = set()
        for index in set(activity.receives):
            senders.update(a for a in data.tx(index).input_addresses if a != address)
        for index in set(activity.spends):
            receivers.update(a for a in data.tx(index).output_addresses if a != address)
        counterparties = senders | receivers
        both_ways = senders & receivers
        # A service sends to and receives from overlapping sets; a collection point
        # mostly receives.
        reciprocity = len(both_ways) / len(counterparties) if counterparties else 0.0
        bidirectional = bool(senders) and bool(receivers)

        records = [data.tx(i) for i in activity.tx_indices]
        gaps = [b.observed_ts - a.observed_ts for a, b in zip(records, records[1:])]
        span_hours = ((records[-1].observed_ts - records[0].observed_ts) / 3600.0
                      if len(records) > 1 else 0.0)

        # Money in from many AND out to many: a pass-through service. A collection
        # point also takes in from many, but sends onward to only a few, so requiring
        # diversity on both sides is what separates the two.
        service_like = (
            bidirectional
            and len(senders) >= config.min_inbound_counterparties
            and len(receivers) >= config.min_outbound_counterparties
        )
        total_flow = max(activity.received_sats, activity.spent_sats)
        flow_balance = (min(activity.received_sats, activity.spent_sats) / total_flow
                        if total_flow else 0.0)
        indicators = {
            "transaction_count": count,
            "case_activity_percentile_cut": percentile_cut,
            "distinct_counterparties": len(counterparties),
            "distinct_senders": len(senders),
            "distinct_receivers": len(receivers),
            "bidirectional_flow": bidirectional,
            "reciprocal_counterparties": len(both_ways),
            "reciprocity_ratio": round(reciprocity, 4),
            "flow_balance": round(flow_balance, 4),
            "min_inbound_counterparties": config.min_inbound_counterparties,
            "min_outbound_counterparties": config.min_outbound_counterparties,
            "activity_span_hours": round(span_hours, 2),
            "median_gap_hours": round(statistics.median(gaps) / 3600.0, 3) if gaps else None,
            "received_sats": activity.received_sats,
            "spent_sats": activity.spent_sats,
            "service_like_profile": service_like,
        }
        findings.append(Finding(
            detector=HIGH_VOLUME_KEY,
            subject_type="address",
            subject_id=address,
            pattern_label="High activity requiring context",
            role_hypothesis="high_activity_service_like" if service_like else "unknown_role",
            severity=RULE_SEVERITY[HIGH_VOLUME_KEY],
            indicators=indicators,
            evidence=[Evidence(
                kind="transaction", txid=r.txid, source_row_id=r.source_row_id,
                row_number=r.row_number, detail={"observed_at": r.observed_at},
            ) for r in records[:50]],
            period_start=records[0].observed_at,
            period_end=records[-1].observed_at,
            alternatives=[
                "An exchange, custodian, payment processor or mining pool transacts at "
                "this volume as a matter of course.",
                "Sustained two-way flow with many distinct counterparties is typical of "
                "a service, not of a single end user.",
            ],
            caveats=[
                "Transaction volume on its own is not evidence of wrongdoing and does "
                "not create a supported pattern.",
                "Identifying this address as a particular business would require "
                "external information the product does not have.",
                CAVEAT_NOT_INTENT,
            ],
            independent_supports=1,
            suppression=(
                "High volume contributes no rule severity. It is reported as context so "
                "that a busy but ordinary address is not ranked as a criminal lead."),
        ))
    return findings


def detect_anomalies(
    data: CaseData,
    config: AnomalyConfig,
    percentiles: dict[str, float],
    addresses_with_pattern: set[str],
) -> list[Finding]:
    """Report unusual addresses the rules did not explain, without inventing a category."""
    findings: list[Finding] = []
    for address, percentile in sorted(percentiles.items()):
        if percentile < config.report_above_percentile:
            continue
        if address in addresses_with_pattern:
            continue  # already explained by a structural detector
        activity = data.addresses.get(address)
        if activity is None:
            continue
        records = [data.tx(i) for i in activity.tx_indices]
        findings.append(Finding(
            detector=ANOMALY_KEY,
            subject_type="address",
            subject_id=address,
            pattern_label="Statistically unusual, no supported pattern",
            role_hypothesis="unknown_role",
            severity=RULE_SEVERITY[ANOMALY_KEY],
            indicators={
                "anomaly_percentile": round(percentile, 2),
                "report_above_percentile": config.report_above_percentile,
                "transaction_count": len(records),
                "received_sats": activity.received_sats,
                "spent_sats": activity.spent_sats,
                "matched_named_pattern": False,
            },
            evidence=[Evidence(
                kind="transaction", txid=r.txid, source_row_id=r.source_row_id,
                row_number=r.row_number, detail={"observed_at": r.observed_at},
            ) for r in records[:25]],
            period_start=records[0].observed_at if records else None,
            period_end=records[-1].observed_at if records else None,
            alternatives=[
                "An unusual measurement can reflect an uncommon but entirely ordinary "
                "usage pattern.",
                "The deviation may be an artefact of how little activity for this "
                "address falls inside the imported period.",
            ],
            caveats=[
                "The model ranks how unusual this address looks against the rest of this "
                "dataset. That is not a probability of wrongdoing.",
                "No named behavioural pattern is claimed. The reason for the deviation "
                "is not established.",
                CAVEAT_NOT_INTENT,
            ],
            independent_supports=1,
        ))
    return findings


def address_endpoint_map(data: CaseData) -> dict[str, set[str]]:
    """Addresses -> endpoints they were observed at. Context only; never an edge."""
    mapping: dict[str, set[str]] = defaultdict(set)
    for ip, indices in data.ip_to_tx.items():
        for index in indices:
            record = data.tx(index)
            for address in set(record.input_addresses) | set(record.output_addresses):
                mapping[address].add(ip)
    return mapping
