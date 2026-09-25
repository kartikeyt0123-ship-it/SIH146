"""The analysis pipeline: sanitised records in, immutable alerts out.

Stage order matters. CoinJoin-like structure is detected first because its result - the
set of collaborative transactions - is what the ownership-merging and collection
detectors must exclude. The model is applied after the rules so that a model failure
leaves the rule findings browsable instead of failing the whole run.

Results are written in one transaction at the end, so a partial run can never appear on
screen as a finished one.
"""
from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import config
from ..db import audit, new_id, now_iso, query_one, write_tx
from ..detectors import clustering, collection, peeling, safeguards, structure
from ..detectors.base import Finding
from ..detectors.config import DetectorConfig
from ..detectors.structure import is_coinjoin_like
from ..features import build_feature_frame, feature_manifest
from ..graphx.model import CaseData, load_case_data
from ..ml.infer import ScoreResult, score_addresses
from ..scoring import evidence_strength, render_explanation, review_priority
from ..scoring.priority import PRIORITY_POLICY

STAGES = (
    "loading",
    "graph",
    "structure_rules",
    "features",
    "model",
    "behaviour_rules",
    "scoring",
    "alerts",
    "complete",
)

#: Detectors whose findings are safeguards: they describe context and are capped so they
#: cannot become high-priority leads on their own.
SAFEGUARD_DETECTORS = {"high_activity_context", "shared_ip_observation"}

#: Detectors that rest on a single heuristic with no second line of support.
SINGLE_HEURISTIC_DETECTORS = {"common_control", "coinjoin_like", "statistical_anomaly",
                              "shared_ip_observation", "high_activity_context"}


class RunCancelled(Exception):
    """Raised when a caller cancels the run between stages."""


@dataclass
class RunResult:
    run_id: str
    alerts_created: int = 0
    findings: list[Finding] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


ProgressCallback = Callable[[str, float, dict[str, Any]], None]


def _noop(stage: str, progress: float, detail: dict[str, Any]) -> None:
    return None


def execute_run(
    run_id: str,
    case_id: str,
    dataset_id: str,
    detector_config: DetectorConfig | None = None,
    model_name: str = "default",
    on_progress: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> RunResult:
    """Run the full analysis. Raises on failure; the caller records that on the run."""
    detector_config = detector_config or DetectorConfig()
    progress = on_progress or _noop
    cancelled = should_cancel or (lambda: False)
    started = time.perf_counter()
    timings: dict[str, float] = {}

    def stage(name: str, fraction: float, detail: dict[str, Any] | None = None) -> None:
        if cancelled():
            raise RunCancelled(f"cancelled before stage {name}")
        timings[name] = time.perf_counter() - started
        progress(name, fraction, detail or {})

    # --- load sanitised records ----------------------------------------------------
    stage("loading", 0.05)
    data: CaseData = load_case_data(case_id, dataset_id)
    coverage = data.coverage()

    stage("graph", 0.15, {"transactions": data.tx_count, "addresses": data.address_count})

    # --- DET01 first: its output gates the ownership heuristics ---------------------
    stage("structure_rules", 0.25)
    coinjoin_findings, collaborative = structure.detect(data, detector_config.coinjoin)

    # --- features and model ---------------------------------------------------------
    stage("features", 0.40)
    frame = build_feature_frame(data, collaborative)

    stage("model", 0.55)
    scores: ScoreResult = score_addresses(frame, bundle_name=model_name)

    # --- remaining behavioural detectors --------------------------------------------
    stage("behaviour_rules", 0.70)
    # The high-volume safeguard runs first so that the collection detector knows which
    # addresses already look like a service. Receiving from many payers is expected of a
    # service, so the collection shape is not informative for those addresses and their
    # severity is reduced before any alert is written.
    high_volume_findings = safeguards.detect_high_volume(data, detector_config.high_volume)
    service_like = {f.subject_id for f in high_volume_findings
                    if f.indicators.get("service_like_profile")}

    collection_findings, participant_findings, collection_stats = collection.detect(
        data, detector_config.collection, collaborative, service_like)
    peel_findings = peeling.detect(data, detector_config.peeling, collaborative)
    cluster_findings, hypotheses = clustering.detect(
        data, detector_config.common_control, collaborative)
    shared_ip_findings = safeguards.detect_shared_ip(data, detector_config.shared_ip)

    named_pattern_addresses = {
        f.subject_id for f in (collection_findings + participant_findings
                               + peel_findings + cluster_findings)
    }
    anomaly_findings = safeguards.detect_anomalies(
        data, detector_config.anomaly, scores.percentiles, named_pattern_addresses)

    findings: list[Finding] = [
        *coinjoin_findings, *collection_findings, *participant_findings,
        *peel_findings, *cluster_findings, *shared_ip_findings,
        *high_volume_findings, *anomaly_findings,
    ]

    # --- scoring ---------------------------------------------------------------------
    stage("scoring", 0.85)
    # A safeguard finding tells the scorer to hold a subject down even when another
    # detector fired for it, so a busy address is not promoted by volume alone.
    suppression_by_subject: dict[str, str] = {
        f.subject_id: f.suppression for f in findings
        if f.detector in SAFEGUARD_DETECTORS and f.suppression
    }

    alert_rows: list[tuple] = []
    evidence_rows: list[tuple] = []
    #: Highest review priority reached by any alert about each address.
    priority_by_address: dict[str, float] = {}
    created_at = now_iso()

    for finding in sorted(findings, key=lambda f: (f.detector, f.subject_id)):
        percentile = (scores.percentiles.get(finding.subject_id)
                      if finding.subject_type == "address" else None)
        unscored_reason = None
        if finding.subject_type == "address" and percentile is None:
            unscored_reason = scores.unscored.get(
                finding.subject_id,
                "No model score is available for this subject."
                if not scores.available else
                "This subject is not an address in the scored population.")

        suppression = finding.suppression
        if finding.detector not in SAFEGUARD_DETECTORS:
            inherited = suppression_by_subject.get(finding.subject_id)
            if inherited and finding.detector in ("statistical_anomaly",):
                suppression = inherited

        result = review_priority(
            rule_severity=finding.severity,
            anomaly_percentile=percentile,
            unscored_reason=unscored_reason,
            suppressed=suppression,
        )
        strength, reasons = evidence_strength(
            independent_supports=finding.independent_supports,
            amount_quality_affected=finding.amount_quality_affected,
            single_heuristic=finding.detector in SINGLE_HEURISTIC_DETECTORS,
            coverage_note=(
                "The model component was unavailable for this subject, so priority "
                "reflects rule severity only." if unscored_reason else None),
        )
        explanation = render_explanation(finding.detector, finding.indicators)

        alert_id = new_id("alert")
        if finding.subject_type == "address":
            priority_by_address[finding.subject_id] = max(
                priority_by_address.get(finding.subject_id, 0.0), result.priority)
        alert_rows.append((
            alert_id, run_id, case_id, finding.subject_type, finding.subject_id,
            finding.detector, finding.pattern_label, finding.role_hypothesis,
            result.priority, result.band,
            json.dumps(result.components, sort_keys=True), strength,
            json.dumps(reasons), finding.period_start, finding.period_end,
            explanation, json.dumps(finding.alternatives), json.dumps(finding.caveats),
            json.dumps(finding.indicators, sort_keys=True, default=str), "new", created_at,
        ))
        for item in finding.evidence:
            evidence_rows.append((
                alert_id, item.kind, item.txid, item.source_row_id, item.row_number,
                json.dumps(item.detail, sort_keys=True, default=str),
            ))

    # --- persist, atomically ----------------------------------------------------------
    stage("alerts", 0.95, {"alerts": len(alert_rows)})

    predictions_path = _write_frozen_predictions(
        run_id, case_id, dataset_id, data, scores, findings, detector_config,
        priority_by_address)

    stats = {
        "coverage": coverage,
        "counts_by_detector": {
            d: sum(1 for f in findings if f.detector == d)
            for d in sorted({f.detector for f in findings})
        },
        "alerts_total": len(alert_rows),
        "addresses_scored": len(scores.scores),
        "addresses_unscored": len(scores.unscored),
        "collaborative_transactions_excluded": len(collaborative),
        "cluster_hypotheses": len(hypotheses),
        "collection_candidates": collection_stats,
        "model_status": scores.model_status,
        "model_error": scores.model_error,
        "priority_policy": PRIORITY_POLICY,
        "feature_manifest": feature_manifest(),
        "stage_seconds": {k: round(v, 3) for k, v in timings.items()},
        "total_seconds": round(time.perf_counter() - started, 3),
    }

    with write_tx() as conn:
        conn.executemany(
            """INSERT INTO alerts (id, run_id, case_id, subject_type, subject_id, pattern,
                   pattern_label, role_hypothesis, priority, priority_band,
                   priority_components, evidence_strength, evidence_reasons,
                   period_start, period_end, explanation, alternatives, caveats,
                   indicators, review_state, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            alert_rows,
        )
        conn.executemany(
            """INSERT INTO alert_evidence (alert_id, kind, txid, source_row_id, row_number, detail_json)
               VALUES (?,?,?,?,?,?)""",
            evidence_rows,
        )
        conn.executemany(
            """INSERT INTO cluster_hypotheses (id, run_id, members_json, edges_json,
                   rule_version, strength, exceptions, decision, decision_note)
               VALUES (?,?,?,?,?,?,?,'open','')""",
            [(h["id"], run_id, json.dumps(h["members"]), json.dumps(h["edges"]),
              h["rule_version"], h["strength"], json.dumps(h["exceptions"]))
             for h in hypotheses],
        )
        conn.executemany(
            """INSERT INTO address_scores (run_id, address, anomaly_score,
                   anomaly_percentile, scored, unscored_reason, features_json)
               VALUES (?,?,?,?,?,?,?)""",
            [(run_id, address,
              scores.scores.get(address), scores.percentiles.get(address),
              1 if address in scores.scores else 0,
              scores.unscored.get(address) if address not in scores.scores else None,
              json.dumps({k: float(v) for k, v in frame.loc[address].items()}, sort_keys=True)
              if address in frame.index else "{}")
             for address in sorted(data.addresses)],
        )
        conn.execute(
            """UPDATE runs SET status='complete', stage='complete', progress=1.0,
                   finished_at=?, stats_json=?, model_version=?, model_status=?,
                   predictions_path=? WHERE id=?""",
            (now_iso(), json.dumps(stats, sort_keys=True, default=str),
             scores.model_version or None, scores.model_status,
             str(predictions_path) if predictions_path else None, run_id),
        )
        audit(conn, "run.completed", case_id, {
            "run_id": run_id, "alerts": len(alert_rows),
            "model_status": scores.model_status,
        })

    stage("complete", 1.0, {"alerts": len(alert_rows)})
    return RunResult(run_id=run_id, alerts_created=len(alert_rows), findings=findings, stats=stats)


def _write_frozen_predictions(
    run_id: str, case_id: str, dataset_id: str, data: CaseData,
    scores: ScoreResult, findings: list[Finding], detector_config: DetectorConfig,
    priority_by_address: dict[str, float],
) -> Any:
    """Freeze this run's predictions for the separate evaluator.

    The evaluator is the only place a ground-truth file is ever read, and it reads this
    file rather than the live database, so predictions cannot be adjusted after labels
    are seen.
    """
    config.ensure_dirs()
    dataset = query_one("SELECT sha256, original_name FROM datasets WHERE id=?", (dataset_id,))

    # Project transaction-level findings onto the addresses that took part in them, so
    # that an address-level benchmark can be computed without the evaluator having to
    # re-derive participation. This is a projection of predictions, not a new prediction.
    tx_patterns_by_address: dict[str, set[str]] = {}
    for finding in findings:
        if finding.subject_type != "transaction":
            continue
        index = data.by_txid.get(finding.subject_id)
        if index is None:
            continue
        record = data.tx(index)
        for address in set(record.input_addresses) | set(record.output_addresses):
            tx_patterns_by_address.setdefault(address, set()).add(finding.detector)

    payload = {
        "run_id": run_id,
        "case_id": case_id,
        "dataset_id": dataset_id,
        "dataset_sha256": dataset["sha256"] if dataset else None,
        "dataset_name": dataset["original_name"] if dataset else None,
        "frozen_at": now_iso(),
        "detector_version": detector_config.version,
        "detector_config": detector_config.to_dict(),
        "model_version": scores.model_version,
        "model_status": scores.model_status,
        "score_direction": "higher_is_more_unusual",
        "priority_policy": PRIORITY_POLICY,
        "address_predictions": [
            {
                "address": address,
                "anomaly_score": scores.scores.get(address),
                "anomaly_percentile": scores.percentiles.get(address),
                "scored": address in scores.scores,
                "unscored_reason": scores.unscored.get(address),
                "patterns": sorted({f.detector for f in findings
                                    if f.subject_type == "address" and f.subject_id == address}),
                "transaction_patterns": sorted(tx_patterns_by_address.get(address, set())),
                # The priority policy is applied to every address, not only to those
                # that raised an alert. An address with no rule hit still has a model
                # score, so its priority is the formula with a rule severity of zero.
                # Leaving it null here would make the combined ranking incomparable
                # with the model-only ranking during evaluation.
                "review_priority": priority_by_address.get(
                    address,
                    review_priority(
                        rule_severity=0.0,
                        anomaly_percentile=scores.percentiles.get(address),
                        unscored_reason=scores.unscored.get(address),
                    ).priority),
                "raised_alert": address in priority_by_address,
                "roles": sorted({f.role_hypothesis for f in findings
                                 if f.subject_type == "address" and f.subject_id == address}),
            }
            for address in sorted(data.addresses)
        ],
        "transaction_predictions": [
            {
                "txid": f.subject_id,
                "patterns": [f.detector],
            }
            for f in sorted(findings, key=lambda f: f.subject_id)
            if f.subject_type == "transaction"
        ],
        "note": (
            "Frozen before any ground truth was joined. The evaluator reads this file; "
            "it does not read or modify the live case database."),
    }
    path = config.PREDICTIONS_DIR / f"{run_id}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path
