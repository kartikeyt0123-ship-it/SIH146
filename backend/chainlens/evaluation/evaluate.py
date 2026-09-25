"""Offline evaluator.

Run:
    python -m chainlens.evaluation.evaluate \
        --predictions chainlens_data/predictions/<run_id>.json \
        --ground-truth data/samples/ps4_ground_truth

This is the only place in the product that reads a ground-truth file, and it reads a
prediction artifact that was frozen before any label was available. It never writes to
the case database, so nothing measured here can flow back into an investigation.

What the numbers mean is stated with them. Every rate is printed with its numerator and
denominator, and results obtained by scoring the population the model was fitted on are
labelled exploratory rather than presented as performance on unseen data.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

#: How detector outputs map onto the supplied binary label.
#:
#: The supplied answer key marks mixing participants and victim-payment addresses as
#: suspicious=1. That definition is kept here so the benchmark is reproducible, but the
#: product itself reports those roles separately: being labelled suspicious in this file
#: does not make an address an offender, and the role breakdown below reports each one
#: on its own so victim participation is never equated with offender attribution.
SUSPICIOUS_PATTERN_MAPPING: dict[str, str] = {
    "collection_pattern": "maps to ransomware_collector",
    "payment_participant": "maps to ransomware_victim_payment",
    "peeling_sequence": "maps to peel_chain_hop",
    "common_control": "maps to same_actor_cluster",
    "coinjoin_like": "maps to coinjoin_mixing (projected from the transaction onto its participants)",
}

#: Detectors that never assert a suspicious classification and so are excluded from the
#: binary benchmark: they exist to provide context or to hold priority down.
EXCLUDED_FROM_BINARY: dict[str, str] = {
    "high_activity_context": "a safeguard; reports context, never a classification",
    "shared_ip_observation": "a safeguard; a network sighting is not a classification",
    "statistical_anomaly": "an unexplained deviation with no named pattern claimed",
}


@dataclass
class BinaryMetrics:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else None

    @property
    def false_positive_rate(self) -> float | None:
        return self.fp / (self.fp + self.tn) if (self.fp + self.tn) else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        return (2 * p * r / (p + r)) if p and r else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "true_positives": self.tp, "false_positives": self.fp,
            "false_negatives": self.fn, "true_negatives": self.tn,
            "positives_in_truth": self.tp + self.fn,
            "negatives_in_truth": self.fp + self.tn,
            "predicted_positive": self.tp + self.fp,
            "precision": self.precision,
            "precision_denominator": self.tp + self.fp,
            "recall": self.recall,
            "recall_denominator": self.tp + self.fn,
            "false_positive_rate": self.false_positive_rate,
            "false_positive_rate_denominator": self.fp + self.tn,
            "f1": self.f1,
        }


@dataclass
class EvaluationReport:
    run_id: str
    generated_at: str
    dataset_sha256: str | None
    model_version: str | None
    model_status: str
    detector_version: str
    coverage: dict[str, Any] = field(default_factory=dict)
    rules_only: dict[str, Any] = field(default_factory=dict)
    ranking: dict[str, Any] = field(default_factory=dict)
    per_pattern: dict[str, Any] = field(default_factory=dict)
    per_role: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "dataset_sha256": self.dataset_sha256,
            "model_version": self.model_version,
            "model_status": self.model_status,
            "detector_version": self.detector_version,
            "coverage": self.coverage,
            "address_benchmark_rules_only": self.rules_only,
            "address_ranking_metrics": self.ranking,
            "per_ground_truth_pattern": self.per_pattern,
            "per_predicted_role": self.per_role,
            "label_mapping": SUSPICIOUS_PATTERN_MAPPING,
            "excluded_from_binary_benchmark": EXCLUDED_FROM_BINARY,
            "notes": self.notes,
        }


def load_ground_truth(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise ValueError(f"ground truth file {path} has no rows")
    required = {"wallet_id", "is_suspicious", "pattern_type"}
    missing = required - set(rows[0])
    if missing:
        raise ValueError(f"ground truth is missing columns: {sorted(missing)}")
    return {r["wallet_id"]: r for r in rows}


def average_precision(labels: np.ndarray, scores: np.ndarray) -> float | None:
    """Area under the precision-recall curve, computed as the step-wise average.

    Implemented here rather than imported so the evaluator has no dependency on the
    modelling stack it is measuring.
    """
    if labels.sum() == 0:
        return None
    order = np.argsort(-scores, kind="stable")
    ordered = labels[order]
    cumulative_tp = np.cumsum(ordered)
    precision = cumulative_tp / np.arange(1, len(ordered) + 1)
    return float((precision * ordered).sum() / labels.sum())


def precision_at_k(labels: np.ndarray, scores: np.ndarray, k: int) -> dict[str, Any]:
    if len(labels) == 0:
        return {"k": k, "precision": None, "hits": 0, "denominator": 0}
    order = np.argsort(-scores, kind="stable")
    top = labels[order][:k]
    return {"k": k, "precision": float(top.mean()) if len(top) else None,
            "hits": int(top.sum()), "denominator": int(len(top))}


def evaluate_predictions(predictions: dict[str, Any], truth: dict[str, dict[str, str]]
                         ) -> EvaluationReport:
    address_rows = predictions.get("address_predictions", [])
    by_address = {row["address"]: row for row in address_rows}

    predicted_ids = set(by_address)
    truth_ids = set(truth)
    joined = sorted(predicted_ids & truth_ids)

    report = EvaluationReport(
        run_id=predictions.get("run_id", "unknown"),
        generated_at=predictions.get("frozen_at", "unknown"),
        dataset_sha256=predictions.get("dataset_sha256"),
        model_version=predictions.get("model_version"),
        model_status=predictions.get("model_status", "unknown"),
        detector_version=predictions.get("detector_version", "unknown"),
    )

    unscored = [a for a in joined if not by_address[a].get("scored", False)]
    report.coverage = {
        "addresses_in_predictions": len(predicted_ids),
        "addresses_in_ground_truth": len(truth_ids),
        "joined_on_wallet_id_to_address": len(joined),
        "in_predictions_only": len(predicted_ids - truth_ids),
        "in_ground_truth_only": len(truth_ids - predicted_ids),
        "joined_but_unscored_by_model": len(unscored),
        "join_note": (
            "wallet_id in the ground-truth file is joined to the address identifier. "
            "These are address identifiers; they are not verified wallets or people."),
        "unscored_note": (
            "Unscored addresses are counted here and are included in the binary "
            "benchmark as rule-based predictions; they are not dropped."),
    }

    # --- binary benchmark from the rules -------------------------------------------
    labels = np.array([1 if truth[a]["is_suspicious"] == "1" else 0 for a in joined])
    binary_detectors = set(SUSPICIOUS_PATTERN_MAPPING)
    predicted = np.array([
        1 if (set(by_address[a].get("patterns", []))
              | set(by_address[a].get("transaction_patterns", []))) & binary_detectors
        else 0
        for a in joined
    ])

    metrics = BinaryMetrics()
    for label, pred in zip(labels, predicted):
        if pred and label:
            metrics.tp += 1
        elif pred and not label:
            metrics.fp += 1
        elif not pred and label:
            metrics.fn += 1
        else:
            metrics.tn += 1
    report.rules_only = {
        **metrics.to_dict(),
        "basis": "structural detectors only; the model contributes no part of this decision",
    }

    # --- ranking comparison: rules-only, model-only, combined ------------------------
    severity_rank = predicted.astype(float)
    model_rank = np.array([
        by_address[a].get("anomaly_percentile") if by_address[a].get("anomaly_percentile") is not None else -1.0
        for a in joined], dtype=float)
    combined_rank = np.array([
        by_address[a].get("review_priority") if by_address[a].get("review_priority") is not None else 0.0
        for a in joined], dtype=float)

    positives = int(labels.sum())
    report.ranking = {
        "positives": positives,
        "negatives": int(len(labels) - positives),
        "base_rate": float(labels.mean()) if len(labels) else None,
        "variants": {},
    }
    for name, scores, note in (
        ("rules_only", severity_rank, "binary rule prediction used as the ranking score"),
        ("model_only", model_rank, "anomaly percentile alone; unscored addresses rank last"),
        ("combined", combined_rank, "review priority: 0.60 x anomaly percentile + 0.40 x rule severity"),
    ):
        report.ranking["variants"][name] = {
            "note": note,
            "pr_auc": average_precision(labels, scores),
            "precision_at_k": [precision_at_k(labels, scores, k) for k in (20, 50, 100)],
        }

    # --- per ground-truth pattern ------------------------------------------------------
    per_pattern: dict[str, Any] = {}
    for address in joined:
        pattern = truth[address]["pattern_type"]
        entry = per_pattern.setdefault(pattern, {
            "support": 0, "flagged_by_rules": 0, "labelled_suspicious": 0,
            "predicted_patterns": {},
        })
        entry["support"] += 1
        entry["labelled_suspicious"] += 1 if truth[address]["is_suspicious"] == "1" else 0
        found = sorted(set(by_address[address].get("patterns", []))
                       | set(by_address[address].get("transaction_patterns", [])))
        if set(found) & binary_detectors:
            entry["flagged_by_rules"] += 1
        for name in found:
            entry["predicted_patterns"][name] = entry["predicted_patterns"].get(name, 0) + 1
    for entry in per_pattern.values():
        entry["recall_within_pattern"] = (
            entry["flagged_by_rules"] / entry["support"] if entry["support"] else None)
    report.per_pattern = dict(sorted(per_pattern.items()))

    # --- per predicted role -------------------------------------------------------------
    per_role: dict[str, Any] = {}
    for address in joined:
        for role in by_address[address].get("roles", []):
            entry = per_role.setdefault(role, {"count": 0, "ground_truth_breakdown": {}})
            entry["count"] += 1
            pattern = truth[address]["pattern_type"]
            entry["ground_truth_breakdown"][pattern] = (
                entry["ground_truth_breakdown"].get(pattern, 0) + 1)
    report.per_role = dict(sorted(per_role.items()))

    report.notes = [
        "Address-level, transaction-level and clustering results are distinct "
        "measurements and are not interchangeable.",
        "The supplied binary label marks mixing participants and victim-payment "
        "addresses as suspicious. That definition is reproduced for comparability; the "
        "per-role breakdown is where victim participation is kept separate from "
        "offender attribution.",
        "Thresholds and score weights were frozen before this file was read.",
    ]
    if predictions.get("model_status") != "used":
        report.notes.append(
            "The model was unavailable for this run, so the model-only and combined "
            "rankings reflect rules alone.")
    return report


def format_report(report: EvaluationReport) -> str:
    out: list[str] = []
    add = out.append
    data = report.to_dict()

    add("=" * 78)
    add("ChainLens offline evaluation")
    add("=" * 78)
    add(f"run            : {report.run_id}")
    add(f"predictions    : frozen at {report.generated_at}")
    add(f"dataset sha256 : {report.dataset_sha256}")
    add(f"detectors      : {report.detector_version}")
    add(f"model          : {report.model_version} ({report.model_status})")

    add("\n-- coverage " + "-" * 66)
    for key, value in report.coverage.items():
        if not key.endswith("note"):
            add(f"  {key:38s} {value}")

    add("\n-- address benchmark, rules only " + "-" * 45)
    m = report.rules_only
    add(f"  TP {m['true_positives']}  FP {m['false_positives']}  "
        f"FN {m['false_negatives']}  TN {m['true_negatives']}")
    for name, denom in (("precision", "precision_denominator"),
                        ("recall", "recall_denominator"),
                        ("false_positive_rate", "false_positive_rate_denominator")):
        value = m[name]
        add(f"  {name:22s} {'n/a' if value is None else f'{value:.4f}'}"
            f"   (denominator {m[denom]})")
    add(f"  f1                     {'n/a' if m['f1'] is None else f'{m[chr(102)+chr(49)]:.4f}'}")

    add("\n-- ranking comparison " + "-" * 56)
    add(f"  positives {report.ranking['positives']} / "
        f"negatives {report.ranking['negatives']} "
        f"(base rate {report.ranking['base_rate']:.4f})")
    for name, variant in report.ranking["variants"].items():
        auc = variant["pr_auc"]
        add(f"  {name:12s} PR-AUC {'n/a' if auc is None else f'{auc:.4f}'}")
        for entry in variant["precision_at_k"]:
            p = entry["precision"]
            add(f"                 P@{entry['k']:<4d} "
                f"{'n/a' if p is None else f'{p:.4f}'} "
                f"({entry['hits']}/{entry['denominator']})")

    add("\n-- recall within each ground-truth pattern " + "-" * 35)
    add(f"  {'pattern':<30} {'support':>8} {'flagged':>8} {'recall':>8}")
    for pattern, entry in report.per_pattern.items():
        recall = entry["recall_within_pattern"]
        add(f"  {pattern:<30} {entry['support']:>8} {entry['flagged_by_rules']:>8} "
            f"{'n/a' if recall is None else f'{recall:>8.4f}'}")

    add("\n-- predicted roles against ground truth " + "-" * 38)
    for role, entry in report.per_role.items():
        add(f"  {role} ({entry['count']})")
        for pattern, count in sorted(entry["ground_truth_breakdown"].items(),
                                     key=lambda kv: -kv[1]):
            add(f"      {pattern:<32} {count}")

    add("\n-- notes " + "-" * 69)
    for note in report.notes:
        add(f"  * {note}")
    add("=" * 78)
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate frozen ChainLens predictions against a ground-truth file.")
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--ground-truth", required=True, type=Path)
    parser.add_argument("--json-out", type=Path, help="Also write the report as JSON.")
    args = parser.parse_args(argv)

    if not args.predictions.exists():
        parser.error(f"predictions file not found: {args.predictions}")
    if not args.ground_truth.exists():
        parser.error(f"ground-truth file not found: {args.ground_truth}")

    predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
    truth = load_ground_truth(args.ground_truth)
    report = evaluate_predictions(predictions, truth)

    print(format_report(report))
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True),
                                 encoding="utf-8")
        print(f"\nJSON report written to {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
