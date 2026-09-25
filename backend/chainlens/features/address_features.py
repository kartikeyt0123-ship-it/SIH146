"""One feature row per address.

Design rules, all of which are enforced by tests:

* **Allowlist.** Only the names in :data:`FEATURE_ORDER` ever reach the model, in that
  exact order. Anything else is not a feature, whatever else the code computes.
* **No identity.** Addresses, TXIDs, IP addresses and script types are identifiers, not
  behaviour. None of them is encoded as a predictive variable - not as a string, a hash
  or a category code. An identifier lets a model memorise a specific dataset instead of
  learning a behaviour.
* **No labels, and no proxies for them.** Nothing derived from an evaluation label can
  appear here, because this module reads only the sanitised tables.
* **No data-quality artefacts.** The amount-discrepancy magnitude is a property of how
  the file was generated, not of anyone's behaviour, so it is excluded from the model
  and kept as evidence metadata instead.
* **Explicit missing values.** Statistics that are undefined for a single-event address
  are set to 0.0 and paired with ``has_multiple_events``, so the model can tell "no gap
  measured" from "a gap of zero".
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

from ..detectors.config import CoinJoinConfig
from ..detectors.structure import equal_value_groups
from ..graphx.model import CaseData

#: Bump whenever a feature is added, removed or redefined.
FEATURE_SCHEMA_VERSION = "address-features/v1"

#: The exact feature vector, in the exact order the model expects.
FEATURE_ORDER: tuple[str, ...] = (
    "tx_count",
    "incoming_tx_count",
    "outgoing_tx_count",
    "distinct_senders",
    "distinct_receivers",
    "distinct_counterparties",
    "received_sats_log",
    "spent_sats_log",
    "mean_incoming_sats_log",
    "incoming_value_concentration",
    "mean_input_count",
    "mean_output_count",
    "equal_output_participation",
    "repeated_co_input_partners",
    "median_inter_event_hours",
    "activity_span_hours",
    "max_tx_in_24h",
    "has_multiple_events",
)

FEATURE_DEFINITIONS: dict[str, str] = {
    "tx_count": "Transactions in the imported period in which the address appears at all.",
    "incoming_tx_count": "Transactions in which the address is an output.",
    "outgoing_tx_count": "Transactions in which the address is an input.",
    "distinct_senders": (
        "Distinct addresses appearing as inputs of transactions that pay this address. "
        "Co-occurrence within a transaction, not a proven payer-to-payee link."),
    "distinct_receivers": (
        "Distinct addresses appearing as outputs of transactions this address funds. "
        "Co-occurrence within a transaction, not a proven payee link."),
    "distinct_counterparties": "Union of distinct senders and receivers.",
    "received_sats_log": "log1p of total satoshis received in the imported period.",
    "spent_sats_log": "log1p of total satoshis contributed as an input.",
    "mean_incoming_sats_log": "log1p of the mean per-transaction amount received.",
    "incoming_value_concentration": (
        "Largest single receipt divided by total received; 0.0 when nothing was "
        "received. 1.0 means all value arrived in one transaction."),
    "mean_input_count": "Mean number of inputs of the transactions the address is in.",
    "mean_output_count": "Mean number of outputs of the transactions the address is in.",
    "equal_output_participation": (
        "Share of the address's transactions whose outputs contain a repeated "
        "denomination group, i.e. an equal-value batch structure."),
    "repeated_co_input_partners": (
        "Number of distinct addresses co-spent with this address in more than one "
        "transaction. Collaborative transactions are excluded from the count."),
    "median_inter_event_hours": (
        "Median gap between consecutive transactions involving the address. "
        "0.0 when the address has a single event; read with has_multiple_events."),
    "activity_span_hours": (
        "Hours between the address's first and last observed transaction. "
        "0.0 for a single event; read with has_multiple_events."),
    "max_tx_in_24h": "Largest number of the address's transactions inside any 24-hour window.",
    "has_multiple_events": (
        "1.0 when the address appears in more than one transaction, otherwise 0.0. "
        "Marks whether the timing features above are defined."),
}

#: Recorded in the manifest so the exclusions are auditable, not just asserted in prose.
EXCLUDED_FROM_MODEL: dict[str, str] = {
    "address_string": "Identity. Encoding it would let the model memorise specific addresses.",
    "txid": "Identity, and unique per row.",
    "src_ip / dst_ip / ports": (
        "Network identity. Kept as displayed context only; an IP does not establish "
        "transaction origin or address ownership."),
    "script_type": "Metadata about the record, and transaction-level rather than address-level.",
    "fee_rate": "Cannot be computed: the contract has no virtual transaction size.",
    "amount_discrepancy_magnitude": (
        "A generation artefact of the supplied file, not behaviour. Excluded so it "
        "cannot become a shortcut for finding planted rows."),
    "is_planted_suspicious / is_suspicious / pattern_type": "Evaluation labels.",
    "ground_truth_membership": "Evaluation label proxy.",
}


def _co_input_partners(data: CaseData, collaborative: set[int]) -> dict[str, int]:
    """Distinct addresses each address was co-spent with in more than one transaction."""
    pair_counts: dict[tuple[str, str], int] = defaultdict(int)
    for record in data.transactions:
        if record.index in collaborative:
            continue
        addresses = sorted(set(record.input_addresses))
        if len(addresses) < 2 or len(addresses) > 12:
            continue
        for i, a in enumerate(addresses):
            for b in addresses[i + 1:]:
                pair_counts[(a, b)] += 1
    repeated: dict[str, set[str]] = defaultdict(set)
    for (a, b), count in pair_counts.items():
        if count >= 2:
            repeated[a].add(b)
            repeated[b].add(a)
    return {address: len(partners) for address, partners in repeated.items()}


def _max_in_window(timestamps: list[float], window_seconds: float) -> int:
    """Largest number of events inside any sliding window."""
    if not timestamps:
        return 0
    best = 1
    left = 0
    for right, ts in enumerate(timestamps):
        while ts - timestamps[left] > window_seconds:
            left += 1
        best = max(best, right - left + 1)
    return best


def build_feature_frame(data: CaseData, collaborative: set[int] | None = None) -> pd.DataFrame:
    """Build the address feature matrix, indexed by address and ordered by FEATURE_ORDER."""
    collaborative = collaborative or set()
    coinjoin_config = CoinJoinConfig()

    # Which transactions contain an equal-value denomination group.
    equal_output_tx: set[int] = set()
    for record in data.transactions:
        amounts = [a for _, a in record.outputs]
        if len(amounts) < coinjoin_config.min_equal_outputs:
            continue
        groups = equal_value_groups(amounts, coinjoin_config.equal_amount_band_sats)
        if groups and len(groups[0]) >= coinjoin_config.min_equal_outputs:
            equal_output_tx.add(record.index)

    repeated_partners = _co_input_partners(data, collaborative)

    rows: list[dict[str, Any]] = []
    for address in sorted(data.addresses):
        activity = data.addresses[address]
        receive_indices = sorted(set(activity.receives))
        spend_indices = sorted(set(activity.spends))
        all_indices = activity.tx_indices
        records = [data.tx(i) for i in all_indices]

        senders: set[str] = set()
        receipts: list[int] = []
        for index in receive_indices:
            record = data.tx(index)
            senders.update(a for a in record.input_addresses if a != address)
            receipts.append(sum(amount for addr, amount in record.outputs if addr == address))

        receivers: set[str] = set()
        for index in spend_indices:
            record = data.tx(index)
            receivers.update(a for a in record.output_addresses if a != address)

        timestamps = sorted(r.observed_ts for r in records)
        gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
        multiple = len(timestamps) > 1
        total_received = sum(receipts)

        rows.append({
            "address": address,
            "tx_count": float(len(all_indices)),
            "incoming_tx_count": float(len(receive_indices)),
            "outgoing_tx_count": float(len(spend_indices)),
            "distinct_senders": float(len(senders)),
            "distinct_receivers": float(len(receivers)),
            "distinct_counterparties": float(len(senders | receivers)),
            "received_sats_log": math.log1p(activity.received_sats),
            "spent_sats_log": math.log1p(activity.spent_sats),
            "mean_incoming_sats_log": math.log1p(total_received / len(receipts)) if receipts else 0.0,
            "incoming_value_concentration": (max(receipts) / total_received) if total_received else 0.0,
            "mean_input_count": statistics.fmean([r.input_count for r in records]) if records else 0.0,
            "mean_output_count": statistics.fmean([r.output_count for r in records]) if records else 0.0,
            "equal_output_participation": (
                sum(1 for i in all_indices if i in equal_output_tx) / len(all_indices)
                if all_indices else 0.0),
            "repeated_co_input_partners": float(repeated_partners.get(address, 0)),
            "median_inter_event_hours": (statistics.median(gaps) / 3600.0) if gaps else 0.0,
            "activity_span_hours": ((timestamps[-1] - timestamps[0]) / 3600.0) if multiple else 0.0,
            "max_tx_in_24h": float(_max_in_window(timestamps, 24 * 3600.0)),
            "has_multiple_events": 1.0 if multiple else 0.0,
        })

    frame = pd.DataFrame(rows)
    if frame.empty:
        frame = pd.DataFrame(columns=["address", *FEATURE_ORDER])
    frame = frame.set_index("address")
    # Reindex to the allowlist: this is what makes the allowlist real rather than a claim.
    frame = frame.reindex(columns=list(FEATURE_ORDER))
    return frame.astype(float)


def feature_manifest() -> dict[str, Any]:
    """The feature contract, stored with every model bundle and every export."""
    return {
        "schema_version": FEATURE_SCHEMA_VERSION,
        "feature_order": list(FEATURE_ORDER),
        "definitions": FEATURE_DEFINITIONS,
        "excluded_from_model": EXCLUDED_FROM_MODEL,
        "missing_value_policy": (
            "Timing statistics are undefined for an address with one event. They are set "
            "to 0.0 and accompanied by has_multiple_events=0.0 so the model can "
            "distinguish 'not measured' from 'measured as zero'. No feature is imputed "
            "from another address."),
        "unit_notes": (
            "Values are integer satoshis before the log transform; times are hours. "
            "Counts are of records in the imported period only and are not a claim about "
            "an address's complete history or balance."),
    }


def assert_no_identity_leak(frame: pd.DataFrame) -> None:
    """Fail loudly if a non-allowlisted or non-numeric column ever reaches the model."""
    extra = [c for c in frame.columns if c not in FEATURE_ORDER]
    if extra:
        raise ValueError(f"features outside the allowlist reached the model: {extra}")
    if list(frame.columns) != list(FEATURE_ORDER):
        raise ValueError("feature order does not match the declared schema")
    if not all(np.issubdtype(dtype, np.number) for dtype in frame.dtypes):
        raise ValueError("a non-numeric column reached the model")
