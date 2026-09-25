"""DET02 - collection behaviour, and DET03 - payment participant context.

A collection pattern is many distinct paying addresses converging on one receiving
address inside a window. That shape is shared by ransom collection, a fundraising
address and a merchant's till, so the product says "collection pattern" and lists the
alternatives rather than naming a crime.

Payers are reported separately, as potential payment participants. A participant never
inherits the collection point's priority: in the behaviour this models, the payers are
usually the victims.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict

from ..graphx.model import CaseData
from .base import (
    CAVEAT_AMOUNT_DISCREPANCY,
    CAVEAT_NOT_INTENT,
    CAVEAT_NO_OWNERSHIP,
    RULE_SEVERITY,
    Evidence,
    Finding,
)
from .config import CollectionConfig

DETECTOR_KEY = "collection_pattern"
PARTICIPANT_KEY = "payment_participant"


def _best_window(events: list[tuple[float, str, int]], window_seconds: float
                 ) -> tuple[int, int, int]:
    """Largest number of distinct payers in any window, as (count, start_idx, end_idx).

    ``events`` is (timestamp, payer_address, tx_index) sorted by time. A two-pointer scan
    keeps this linear in the number of receiving events.
    """
    best = (0, 0, 0)
    counts: dict[str, int] = defaultdict(int)
    left = 0
    for right, (ts, payer, _) in enumerate(events):
        counts[payer] += 1
        while ts - events[left][0] > window_seconds:
            counts[events[left][1]] -= 1
            if counts[events[left][1]] == 0:
                del counts[events[left][1]]
            left += 1
        if len(counts) > best[0]:
            best = (len(counts), left, right)
    return best


#: Severity ceiling applied when an address independently shows a service-like profile.
#: A business that receives from many customers produces the collection shape as a
#: matter of course, so the shape stops being informative about that address.
SERVICE_LIKE_SEVERITY_CEILING = 30.0


def grade_severity(indicators: dict, config: CollectionConfig,
                   service_like: bool = False) -> tuple[float, list[str]]:
    """Grade a collection candidate, returning the severity and what produced it.

    Payer count alone does not decide this. The count sets a base, which saturates
    quickly because "many payers" stops being more informative once it is clearly many,
    and corroborating structure adds to it. Each contribution is returned so the alert
    can show the analyst exactly how the number was reached.
    """
    reasons: list[str] = []
    ratio = indicators["distinct_payers"] / max(1, config.min_distinct_payers)
    base = min(60.0, max(0.0, 20.0 * math.log2(ratio) + 20.0))
    reasons.append(
        f"{indicators['distinct_payers']} payers against a floor of "
        f"{config.min_distinct_payers} contributes {base:.0f}.")

    severity = base

    # Corroboration is only counted when there is enough activity for it to mean
    # anything. On a quiet address "all the value arrived in this window" and "it was
    # spent on to one destination" are true almost by default, so awarding credit for
    # them would promote ordinary low-activity addresses.
    contributing = indicators["contributing_transactions"]
    corroboration_eligible = contributing >= config.min_transactions_for_corroboration
    if not corroboration_eligible:
        reasons.append(
            f"Only {contributing} transactions contribute, below the "
            f"{config.min_transactions_for_corroboration} needed for the corroborating "
            f"signals to be meaningful, so no corroboration credit is given.")
    else:
        if indicators.get("payments_similar_in_size"):
            severity += 8.0
            reasons.append("Payments similar in size: +8.")
        if indicators["observed_span_hours"] <= config.window_hours / 3.0:
            severity += 7.0
            reasons.append(
                f"Payments arrived inside {indicators['observed_span_hours']:.1f} hours, a "
                f"burst relative to the {config.window_hours:.0f}-hour window: +7.")
        # Onward movement counts only when most of what was collected actually moved on
        # to a handful of destinations.
        moved_on = indicators["incoming_sats_in_window"] and (
            indicators["subsequent_outflow_sats"]
            >= config.onward_movement_ratio * indicators["incoming_sats_in_window"])
        if (moved_on
                and indicators["distinct_outflow_destinations"] <= config.concentrated_outflow_destinations):
            severity += 10.0
            reasons.append(
                f"{indicators['subsequent_outflow_sats'] / indicators['incoming_sats_in_window'] * 100:.0f}% "
                f"of the collected value moved on to only "
                f"{indicators['distinct_outflow_destinations']} destination(s): +10.")
        if indicators["inbound_concentration"] >= 0.8:
            severity += 5.0
            reasons.append(
                f"{indicators['inbound_concentration'] * 100:.0f}% of this address's incoming "
                f"activity falls in this window: +5.")

    severity = min(RULE_SEVERITY[DETECTOR_KEY], severity)

    if service_like:
        uncapped = severity
        severity = min(severity, SERVICE_LIKE_SEVERITY_CEILING)
        reasons.append(
            f"This address independently shows a service-like profile (sustained "
            f"two-way flow with many distinct counterparties). Receiving from many "
            f"payers is expected of such an address, so the collection shape is not "
            f"informative here: severity reduced from {uncapped:.0f} to {severity:.0f}.")
    else:
        reasons.append(f"Capped at the collection rule's maximum severity of "
                       f"{RULE_SEVERITY[DETECTOR_KEY]:.0f}; final severity {severity:.0f}.")
    return severity, reasons


def detect(data: CaseData, config: CollectionConfig, collaborative: set[int],
           service_like: set[str] | None = None
           ) -> tuple[list[Finding], list[Finding], dict]:
    """Detect collection points and the payment participants associated with them.

    Returns (collection findings, participant findings, statistics).
    """
    window_seconds = config.window_hours * 3600.0
    service_like = service_like or set()
    collection_findings: list[Finding] = []
    participant_findings: list[Finding] = []
    stats = {"candidates": 0, "below_report_threshold": 0,
             "min_report_severity": config.min_report_severity}

    for address, activity in sorted(data.addresses.items()):
        if not activity.receives:
            continue

        # Build the receiving events, skipping collaborative transactions: the many
        # inputs of a CoinJoin are not "payers converging" on its outputs.
        events: list[tuple[float, str, int]] = []
        for tx_index in sorted(set(activity.receives)):
            if tx_index in collaborative:
                continue
            record = data.tx(tx_index)
            for payer in dict.fromkeys(record.input_addresses):
                if payer == address:
                    continue  # self-spend / change, not an incoming payment
                events.append((record.observed_ts, payer, tx_index))
        if not events:
            continue
        events.sort(key=lambda e: (e[0], e[1], e[2]))

        payer_count, left, right = _best_window(events, window_seconds)
        if payer_count < config.min_distinct_payers:
            continue

        window_events = events[left:right + 1]
        tx_indices = sorted({idx for _, _, idx in window_events})
        payers = sorted({payer for _, payer, _ in window_events})
        records = [data.tx(i) for i in tx_indices]

        # Value received by this address in the window, and how similar the payments are.
        amounts: list[int] = []
        for record in records:
            amounts.append(sum(amount for addr, amount in record.outputs if addr == address))
        total_in = sum(amounts)
        mean = statistics.fmean(amounts) if amounts else 0.0
        cv = (statistics.pstdev(amounts) / mean) if len(amounts) > 1 and mean else 0.0

        window_start, window_end = records[0].observed_at, records[-1].observed_at
        span_hours = (records[-1].observed_ts - records[0].observed_ts) / 3600.0

        # What happened to the funds afterwards: a later outflow is part of the picture.
        later_spends = [data.tx(i) for i in sorted(set(activity.spends))
                        if data.tx(i).observed_ts >= records[0].observed_ts]
        outflow_sats = sum(
            amount for r in later_spends for addr, amount in r.inputs if addr == address)
        outflow_destinations = sorted({
            addr for r in later_spends for addr, _ in r.outputs if addr != address})

        # Concentration: does this address's inbound activity dominate its behaviour?
        total_receives = len({i for i in activity.receives})
        concentration = len(tx_indices) / total_receives if total_receives else 0.0

        quality_affected = any(r.has_amount_discrepancy for r in records)
        indicators = {
            "distinct_payers": payer_count,
            "window_hours": config.window_hours,
            "observed_span_hours": round(span_hours, 2),
            "contributing_transactions": len(tx_indices),
            "incoming_sats_in_window": total_in,
            "mean_payment_sats": round(mean, 2),
            "payment_amount_cv": round(cv, 4),
            "payments_similar_in_size": bool(cv and cv <= config.similar_amount_cv),
            "inbound_concentration": round(concentration, 4),
            "subsequent_outflow_sats": outflow_sats,
            "subsequent_outflow_transactions": len(later_spends),
            "distinct_outflow_destinations": len(outflow_destinations),
            "min_distinct_payers_threshold": config.min_distinct_payers,
        }

        stats["candidates"] += 1
        is_service_like = address in service_like
        indicators["service_like_profile"] = is_service_like
        severity, severity_reasons = grade_severity(indicators, config, is_service_like)
        indicators["graded_severity"] = round(severity, 2)
        indicators["severity_breakdown"] = severity_reasons
        if severity < config.min_report_severity:
            # Recorded in the run statistics, not silently dropped: the analyst can see
            # how many candidates cleared the payer floor but not the evidence bar.
            stats["below_report_threshold"] += 1
            continue

        caveats = [CAVEAT_NOT_INTENT, CAVEAT_NO_OWNERSHIP]
        if quality_affected:
            caveats.append(CAVEAT_AMOUNT_DISCREPANCY)
        if is_service_like:
            caveats.append(
                "This address also shows a service-like profile - sustained two-way flow "
                "with many distinct counterparties. That is the ordinary behaviour of an "
                "exchange or payment processor and is the most likely explanation for "
                "the convergence seen here, so this finding has been held down "
                "accordingly.")

        evidence = [Evidence(
            kind="transaction", txid=r.txid, source_row_id=r.source_row_id,
            row_number=r.row_number,
            detail={
                "observed_at": r.observed_at,
                "payers": [a for a in r.input_addresses if a != address],
                "amount_to_subject_sats": sum(a for ad, a in r.outputs if ad == address),
                "amount_discrepancy": r.has_amount_discrepancy,
            },
        ) for r in records]

        collection_findings.append(Finding(
            detector=DETECTOR_KEY,
            subject_type="address",
            subject_id=address,
            pattern_label="Collection pattern",
            role_hypothesis="collection_point",
            severity=severity,
            indicators=indicators,
            evidence=evidence,
            period_start=window_start,
            period_end=window_end,
            alternatives=[
                "A merchant, exchange deposit address or payment processor receiving from "
                "many customers produces the same shape.",
                "A fundraising or donation address also collects from many unrelated "
                "payers.",
                "Without independent case context, the reason for the convergence is "
                "not established by these records.",
            ],
            caveats=caveats,
            independent_supports=len(tx_indices),
            amount_quality_affected=quality_affected,
        ))

        # DET03: each payer is reported in its own right, at its own (low) severity.
        #
        # Participants are taken from every payment to this collection point, not only
        # from the busiest window. The window exists to decide whether the address is a
        # collection point at all; once it is, a payer who paid a day outside that window
        # is just as much a participant, and omitting them would hide people from the
        # analyst for an arbitrary reason.
        all_incoming = [data.tx(i) for i in sorted(set(activity.receives))
                        if i not in collaborative]
        all_payers = sorted({p for r in all_incoming for p in r.input_addresses
                             if p != address})
        for payer in all_payers:
            payer_records = [r for r in all_incoming if payer in r.input_addresses]
            participant_findings.append(Finding(
                detector=PARTICIPANT_KEY,
                subject_type="address",
                subject_id=payer,
                pattern_label="Potential payment participant",
                role_hypothesis="potential_payment_participant",
                severity=RULE_SEVERITY[PARTICIPANT_KEY],
                indicators={
                    "collection_point": address,
                    "payments_to_collection_point": len(payer_records),
                    "paid_sats": sum(
                        a for r in payer_records for ad, a in r.outputs if ad == address),
                    "collection_window_start": window_start,
                    "collection_window_end": window_end,
                },
                evidence=[Evidence(
                    kind="transaction", txid=r.txid, source_row_id=r.source_row_id,
                    row_number=r.row_number,
                    detail={"observed_at": r.observed_at, "collection_point": address},
                ) for r in payer_records],
                period_start=payer_records[0].observed_at if payer_records else window_start,
                period_end=payer_records[-1].observed_at if payer_records else window_end,
                alternatives=[
                    "Paying an address that also receives from many others is ordinary "
                    "customer or donor behaviour.",
                    "In the behaviour this pattern models, the paying party is commonly "
                    "the injured party rather than an offender.",
                ],
                caveats=[
                    "A participant role describes a position in an observed payment, not "
                    "conduct. This address does not inherit the collection point's "
                    "review priority.",
                    CAVEAT_NO_OWNERSHIP,
                ],
                independent_supports=len(payer_records),
                amount_quality_affected=any(r.has_amount_discrepancy for r in payer_records),
            ))

    stats["reported"] = len(collection_findings)
    stats["participants"] = len(participant_findings)
    return collection_findings, participant_findings, stats
