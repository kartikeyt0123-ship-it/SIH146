"""Detector behaviour: each pattern with a positive, a negative and a lookalike."""
from __future__ import annotations

import pytest

import fixtures
from chainlens.detectors import clustering, collection, peeling, safeguards, structure
from chainlens.detectors.config import DetectorConfig
from chainlens.graphx.model import load_case_data
from chainlens.ingest import ValidationMode, import_dataset


def analyse(case_id: str, data: bytes, name: str = "fixture.csv",
            mode: ValidationMode = ValidationMode.COMPATIBILITY):
    """Import a fixture and return (CaseData, collaborative tx indices, config)."""
    summary = import_dataset(case_id, data, name, mode)
    case_data = load_case_data(case_id, summary.dataset_id)
    config = DetectorConfig()
    _, collaborative = structure.detect(case_data, config.coinjoin)
    return case_data, collaborative, config


# --------------------------------------------------------------- DET01 CoinJoin-like
def test_coinjoin_structure_is_detected(case_id):
    data, collaborative, config = analyse(case_id, fixtures.coinjoin_like(participants=6))
    findings, marked = structure.detect(data, config.coinjoin)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.pattern_label == "CoinJoin-like structure"
    assert finding.indicators["equal_output_count"] == 6
    assert finding.indicators["input_count"] == 6
    assert marked == {0}

    # The label describes the structure, not a crime, and the explanation must not
    # accuse. The disclaimer belongs in the caveats, where it is expected to appear.
    assert "launder" not in finding.pattern_label.lower()
    assert "launder" not in " ".join(finding.alternatives).lower()
    assert any("does not establish intent" in c for c in finding.caveats)
    assert any("merging is suppressed" in c for c in finding.caveats), \
        "the transaction must be excluded from ownership merging"


def test_benign_equal_value_batch_is_not_called_collaborative(case_id):
    """A payroll batch has equal outputs but one payer; it must not be tagged."""
    data, _, config = analyse(case_id, fixtures.benign_equal_value_batch(recipients=6))
    findings, marked = structure.detect(data, config.coinjoin)
    assert findings == [], "a single-input batch is not a collaborative transaction"
    assert marked == set()


def test_conservation_discrepancy_is_not_a_shortcut_to_the_structure(case_id):
    """Fee residuals must not by themselves produce a structural finding."""
    data, _, config = analyse(case_id, fixtures.fee_discrepancy_rows())
    findings, _ = structure.detect(data, config.coinjoin)
    assert findings == []
    assert any(t.has_amount_discrepancy for t in data.transactions), \
        "the fixture should contain discrepancies for this test to be meaningful"


# ------------------------------------------------------------- DET02/03 collection
def test_collection_burst_is_detected_with_participants(case_id):
    data, collaborative, config = analyse(case_id, fixtures.collection_burst(payers=9))
    found, participants, stats = collection.detect(data, config.collection, collaborative)

    subjects = {f.subject_id for f in found}
    assert "COLLECTOR" in subjects
    finding = next(f for f in found if f.subject_id == "COLLECTOR")
    assert finding.indicators["distinct_payers"] >= 9
    assert finding.role_hypothesis == "collection_point"
    assert finding.pattern_label == "Collection pattern"
    assert stats["candidates"] >= 1

    payer_subjects = {f.subject_id for f in participants if f.subject_id.startswith("VICTIM")}
    assert len(payer_subjects) == 9
    participant = next(f for f in participants if f.subject_id == "VICTIM0")
    assert participant.role_hypothesis == "potential_payment_participant"
    assert participant.severity < finding.severity, \
        "a participant must not inherit the collection point's severity"


def test_service_profile_holds_the_collection_finding_down(case_id):
    """The legitimate lookalike: in from many, out to many."""
    data, collaborative, config = analyse(case_id, fixtures.merchant_like_service(customers=40))
    high_volume = safeguards.detect_high_volume(data, config.high_volume)
    service_like = {f.subject_id for f in high_volume if f.indicators["service_like_profile"]}
    assert "SERVICE" in service_like, "a pass-through address must be recognised as service-like"

    found, _, _ = collection.detect(data, config.collection, collaborative, service_like)
    service_findings = [f for f in found if f.subject_id == "SERVICE"]
    if service_findings:
        assert service_findings[0].severity <= collection.SERVICE_LIKE_SEVERITY_CEILING
        assert any("service-like" in c for c in service_findings[0].caveats)


def test_coinjoin_outputs_are_not_counted_as_converging_payers(case_id):
    """A CoinJoin's many inputs must not make each output a collection point."""
    data, collaborative, config = analyse(case_id, fixtures.coinjoin_like(participants=8))
    found, participants, _ = collection.detect(data, config.collection, collaborative)
    assert found == [], "collaborative transactions must be excluded from collection"
    assert participants == []


# ------------------------------------------------------------------- DET04 peeling
def test_peel_chain_is_detected_and_every_address_is_reported(case_id):
    data, collaborative, config = analyse(case_id, fixtures.peel_chain(hops=5))
    findings = peeling.detect(data, config.peeling, collaborative)
    assert findings, "the peel chain should be found"

    roles = {f.role_hypothesis for f in findings}
    assert "sequence_continuation" in roles
    assert "sequence_side_recipient" in roles

    continuation = [f for f in findings if f.role_hypothesis == "sequence_continuation"]
    side = [f for f in findings if f.role_hypothesis == "sequence_side_recipient"]
    assert continuation[0].severity > side[0].severity, \
        "a side recipient is a weaker association than carrying the balance forward"

    finding = continuation[0]
    assert finding.indicators["hop_count"] >= config.peeling.min_chain_length
    assert all(s >= config.peeling.min_dominant_share
               for s in finding.indicators["dominant_output_shares"])
    assert any("previous-output references" in c for c in finding.caveats), \
        "the absence of prevouts must be stated"


def test_broken_chain_is_not_linked_across_a_large_time_gap(case_id):
    data, collaborative, config = analyse(case_id, fixtures.broken_peel_chain(gap_hours=400))
    findings = peeling.detect(data, config.peeling, collaborative)
    assert findings == [], "hops beyond the configured gap must not be joined"


def test_short_chain_below_minimum_length_is_not_reported(case_id):
    data, collaborative, config = analyse(case_id, fixtures.peel_chain(hops=1))
    findings = peeling.detect(data, config.peeling, collaborative)
    assert findings == []


# ------------------------------------------------------------ DET05 common control
def test_repeated_co_spend_creates_a_reversible_hypothesis(case_id):
    data, collaborative, config = analyse(case_id, fixtures.repeated_co_spend(times=3))
    findings, hypotheses = clustering.detect(data, config.common_control, collaborative)
    assert len(hypotheses) == 1
    hypothesis = hypotheses[0]
    assert set(hypothesis["members"]) == {"WALLET_A", "WALLET_B"}
    assert hypothesis["edges"][0]["shared_transaction_count"] == 3
    assert hypothesis["edges"][0]["supporting_txids"]
    assert hypothesis["rule_version"]
    for finding in findings:
        assert any("not verified ownership" in c.lower() or "does not establish" in c.lower()
                   for c in finding.caveats)


def test_single_co_spend_is_not_enough(case_id):
    data, collaborative, config = analyse(case_id, fixtures.repeated_co_spend(times=1))
    findings, hypotheses = clustering.detect(data, config.common_control, collaborative)
    assert hypotheses == [], "one shared transaction must not create an association"
    assert findings == []


def test_collaborative_transactions_are_excluded_from_clustering(case_id):
    """The central exclusion: co-spending inside a CoinJoin proves nothing."""
    data, collaborative, config = analyse(
        case_id, fixtures.co_spend_only_inside_coinjoin(participants=6))
    assert collaborative, "the fixture must be recognised as collaborative first"
    findings, hypotheses = clustering.detect(data, config.common_control, collaborative)
    assert hypotheses == [], "CoinJoin participants must never be merged into one actor"
    assert findings == []


# -------------------------------------------------------------- DET06 shared-IP
def test_shared_ip_never_merges_addresses(case_id):
    """The mutation test: unrelated addresses given the same observation endpoint."""
    data, collaborative, config = analyse(case_id, fixtures.shared_ip_unrelated(addresses=5))

    ip_findings = safeguards.detect_shared_ip(data, config.shared_ip)
    assert ip_findings, "the shared endpoint should be surfaced to the analyst"
    finding = ip_findings[0]
    assert finding.severity == 0.0, "a network sighting must contribute no severity"
    assert finding.indicators["creates_ownership_edge"] is False
    assert any("NOT been grouped" in c for c in finding.caveats)

    # And nothing may cluster them: they never co-spend.
    _, hypotheses = clustering.detect(data, config.common_control, collaborative)
    assert hypotheses == [], "a shared IP must never produce an ownership cluster"


def test_shared_ip_requires_recurring_addresses(case_id):
    """One transaction already involves several addresses; that is not a shared endpoint."""
    builder = fixtures.Builder()
    builder.tx(inputs=[("SOLO_A", 10_000_000), ("SOLO_B", 10_000_000)],
               outputs=[("SOLO_C", 9_000_000), ("SOLO_D", 10_900_000)],
               src_ip="198.51.100.5")
    data, _, config = analyse(case_id, builder.build())
    assert safeguards.detect_shared_ip(data, config.shared_ip) == []


# ------------------------------------------------------------ DET07 high volume
def test_high_volume_alone_produces_context_not_severity(case_id):
    data, _, config = analyse(case_id, fixtures.high_volume_but_ordinary(transactions=120))
    findings = safeguards.detect_high_volume(data, config.high_volume)
    busy = [f for f in findings if f.subject_id == "BUSY"]
    assert busy, "the busy address should be reported as context"
    finding = busy[0]
    assert finding.severity == 0.0
    assert finding.suppression, "the finding must carry an explicit suppression reason"
    assert finding.pattern_label == "High activity requiring context"
    joined = " ".join(finding.caveats).lower()
    assert "not evidence of wrongdoing" in joined


# ------------------------------------------------------- DET08 anomaly / unknown
def test_anomaly_detector_leaves_explained_addresses_alone(case_id):
    data, _, config = analyse(case_id, fixtures.collection_burst(payers=9))
    percentiles = {address: 99.9 for address in data.addresses}
    findings = safeguards.detect_anomalies(
        data, config.anomaly, percentiles, addresses_with_pattern={"COLLECTOR"})
    subjects = {f.subject_id for f in findings}
    assert "COLLECTOR" not in subjects
    if findings:
        assert findings[0].indicators["matched_named_pattern"] is False
        assert any("not a probability" in c for c in findings[0].caveats)


def test_below_threshold_anomalies_are_not_reported(case_id):
    data, _, config = analyse(case_id, fixtures.collection_burst(payers=9))
    percentiles = {address: 50.0 for address in data.addresses}
    assert safeguards.detect_anomalies(data, config.anomaly, percentiles, set()) == []


# ------------------------------------------------------------------ configurability
def test_thresholds_are_configurable(case_id):
    """Raising the minimum chain length must change what is reported."""
    from chainlens.detectors.config import PeelingConfig

    data, collaborative, _ = analyse(case_id, fixtures.peel_chain(hops=4))
    permissive = peeling.detect(data, PeelingConfig(min_chain_length=3), collaborative)
    strict = peeling.detect(data, PeelingConfig(min_chain_length=20), collaborative)
    assert permissive and not strict


def test_detector_config_round_trips_through_a_partial_mapping():
    config = DetectorConfig.from_dict({"collection": {"min_distinct_payers": 12}})
    assert config.collection.min_distinct_payers == 12
    # Unspecified groups keep their documented defaults.
    assert config.coinjoin.min_equal_outputs == DetectorConfig().coinjoin.min_equal_outputs
    assert config.to_dict()["collection"]["min_distinct_payers"] == 12
