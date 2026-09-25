"""Detector thresholds.

Every value here is a configurable starting hypothesis, not a measured optimum, and
none of them were fitted against the supplied answer key. They are chosen from the
published descriptions of each behaviour (for example, a CoinJoin is defined by
repeated equal-value outputs) and are recorded with each run so that a result can
always be reproduced with the settings that produced it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

#: Bump when a detector's behaviour changes in a way that alters results.
DETECTOR_VERSION = "detectors-2026.09.1"


@dataclass(frozen=True, slots=True)
class CoinJoinConfig:
    """DET01 - CoinJoin-like structure."""

    min_inputs: int = 3
    min_outputs: int = 3
    #: How many outputs must share (near-)identical value for the structure to hold.
    min_equal_outputs: int = 3
    #: Values within this many satoshis count as the same denomination.
    equal_amount_band_sats: int = 1


@dataclass(frozen=True, slots=True)
class CollectionConfig:
    """DET02 - many distinct payers converging on one address."""

    min_distinct_payers: int = 5
    window_hours: float = 72.0
    #: Coefficient of variation below this suggests payments of a similar size.
    similar_amount_cv: float = 0.5
    #: Payer count alone is a weak signal: on a realistic population many ordinary
    #: addresses clear any low count threshold. Severity is therefore graded from how
    #: far the evidence exceeds the floor plus corroborating signals, and a candidate is
    #: only raised as an alert at or above this graded severity. Candidates below the
    #: bar are counted in the run statistics rather than hidden.
    min_report_severity: float = 40.0
    #: Onward movement to at most this many destinations counts as concentrated.
    concentrated_outflow_destinations: int = 3
    #: Corroborating signals are only credited above this much contributing activity.
    #: Below it they are near-automatic and say nothing about the address.
    min_transactions_for_corroboration: int = 5
    #: Share of collected value that must move on for onward movement to count.
    onward_movement_ratio: float = 0.5


@dataclass(frozen=True, slots=True)
class PeelingConfig:
    """DET04 - candidate peeling sequence."""

    min_chain_length: int = 3          # connected transactions
    min_dominant_share: float = 0.80   # largest output share of total output value
    max_gap_hours: float = 72.0


@dataclass(frozen=True, slots=True)
class CommonControlConfig:
    """DET05 - candidate common control from repeated co-spending."""

    #: A single shared transaction is not evidence; the co-spend must repeat.
    min_shared_transactions: int = 2
    #: Transactions with at least this many inputs are treated as possibly collaborative
    #: and are excluded from co-input merging even if not tagged CoinJoin-like.
    collaborative_input_threshold: int = 5
    max_cluster_size: int = 200


@dataclass(frozen=True, slots=True)
class SharedIpConfig:
    """DET06 - shared network observation safeguard."""

    #: Addresses that appear at the endpoint in at least this many separate records.
    #: Counting every address seen at an endpoint is meaningless - one transaction
    #: already contributes several - so only recurring addresses count.
    min_observations_per_address: int = 2
    min_recurring_addresses: int = 3
    #: An endpoint must itself have been observed at least this many times.
    min_observations: int = 3
    #: Above this, an endpoint looks like shared infrastructure rather than one actor.
    infrastructure_address_count: int = 20


@dataclass(frozen=True, slots=True)
class HighVolumeConfig:
    """DET07 - legitimate high-volume safeguard."""

    #: An address is "high activity" above this percentile of the case's own population.
    activity_percentile: float = 99.0
    min_transactions: int = 50
    min_distinct_counterparties: int = 20
    #: What separates a service from a collection point is where the money goes, not
    #: where it comes from. Both funnel value in from many payers; only a service pays
    #: it back out to many distinct parties. A collection point sends onward to a
    #: handful of addresses. Requiring diversity on BOTH sides is therefore the test.
    min_inbound_counterparties: int = 20
    min_outbound_counterparties: int = 20
    #: Reported for context. A pass-through moves out roughly what it takes in.
    balanced_flow_ratio: float = 0.5


@dataclass(frozen=True, slots=True)
class AnomalyConfig:
    """DET08 - model-driven anomalies without a named pattern."""

    report_above_percentile: float = 99.0


@dataclass(frozen=True, slots=True)
class DetectorConfig:
    version: str = DETECTOR_VERSION
    coinjoin: CoinJoinConfig = field(default_factory=CoinJoinConfig)
    collection: CollectionConfig = field(default_factory=CollectionConfig)
    peeling: PeelingConfig = field(default_factory=PeelingConfig)
    common_control: CommonControlConfig = field(default_factory=CommonControlConfig)
    shared_ip: SharedIpConfig = field(default_factory=SharedIpConfig)
    high_volume: HighVolumeConfig = field(default_factory=HighVolumeConfig)
    anomaly: AnomalyConfig = field(default_factory=AnomalyConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "DetectorConfig":
        """Build a config from a partial mapping, keeping defaults for absent keys."""
        if not payload:
            return cls()
        groups = {
            "coinjoin": CoinJoinConfig, "collection": CollectionConfig,
            "peeling": PeelingConfig, "common_control": CommonControlConfig,
            "shared_ip": SharedIpConfig, "high_volume": HighVolumeConfig,
            "anomaly": AnomalyConfig,
        }
        kwargs: dict[str, Any] = {"version": payload.get("version", DETECTOR_VERSION)}
        for name, klass in groups.items():
            supplied = payload.get(name) or {}
            allowed = {f for f in klass.__dataclass_fields__}
            kwargs[name] = klass(**{k: v for k, v in supplied.items() if k in allowed})
        return cls(**kwargs)
