"""Review priority and evidence strength.

Three concepts are kept apart on purpose, because collapsing them is how a ranking
starts to look like a verdict:

* **Anomaly percentile** - where an address sits against a frozen reference population.
  A measurement of unusualness, nothing more.
* **Review priority** - a 0-100 policy score for ordering an analyst's queue. A
  deliberate operational choice, not a learned quantity and not a probability.
* **Evidence strength** - low/medium/high, from how much independent support a finding
  has and how good the coverage is. Never a probability of wrongdoing; "high" does not
  prove ownership or intent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Weights for the priority policy. Provisional, frozen before evaluation.
ANOMALY_WEIGHT = 0.60
SEVERITY_WEIGHT = 0.40

HIGH_THRESHOLD = 75.0
MEDIUM_THRESHOLD = 40.0

#: The highest priority a safeguarded finding may reach. Volume alone, or a shared
#: endpoint alone, must never produce a high-priority lead.
SUPPRESSED_CEILING = MEDIUM_THRESHOLD + 20.0  # 60.0, firmly inside the medium band

PRIORITY_POLICY: dict[str, Any] = {
    "formula": "priority = 0.60 * anomaly_percentile + 0.40 * max(rule_severity)",
    "components_scale": "each component is 0-100",
    "rule_combination": "maximum of the firing rules, never a sum",
    "bands": {"high": ">= 75", "medium": ">= 40 and < 75", "low": "< 40"},
    "missing_model_score": (
        "An address with no model score is 'unscored', never scored as zero. Its "
        "priority is the rule severity alone and the alert says the model component "
        "was unavailable."),
    "suppression": (
        f"Findings from a safeguard (high activity, shared endpoint) carry zero rule "
        f"severity and are capped at {SUPPRESSED_CEILING:.0f} so that they cannot enter "
        f"the high band on an anomaly measurement alone."),
    "not_a_probability": (
        "Priority orders a work queue. It is not a probability of wrongdoing and is not "
        "calibrated against any outcome."),
}


@dataclass(slots=True)
class PriorityResult:
    priority: float
    band: str
    components: dict[str, Any] = field(default_factory=dict)


def band_for(priority: float) -> str:
    if priority >= HIGH_THRESHOLD:
        return "high"
    if priority >= MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def review_priority(
    rule_severity: float,
    anomaly_percentile: float | None,
    unscored_reason: str | None = None,
    suppressed: str | None = None,
) -> PriorityResult:
    """Combine the rule and model components under the documented policy."""
    components: dict[str, Any] = {
        "rule_severity": round(rule_severity, 2),
        "rule_weight": SEVERITY_WEIGHT,
        "anomaly_percentile": None if anomaly_percentile is None else round(anomaly_percentile, 2),
        "anomaly_weight": ANOMALY_WEIGHT,
        "formula": PRIORITY_POLICY["formula"],
    }

    if anomaly_percentile is None:
        # No model score: the rule component stands alone rather than being averaged
        # against an invented zero, which would understate a genuine rule hit.
        priority = rule_severity
        components["model_component"] = "unavailable"
        components["unscored_reason"] = unscored_reason or "no model score for this subject"
        components["applied_formula"] = "priority = rule_severity (model component unavailable)"
    else:
        priority = ANOMALY_WEIGHT * anomaly_percentile + SEVERITY_WEIGHT * rule_severity
        components["model_component"] = "used"
        components["applied_formula"] = PRIORITY_POLICY["formula"]

    if suppressed:
        capped = min(priority, SUPPRESSED_CEILING)
        if capped < priority:
            components["suppression_applied"] = {
                "ceiling": SUPPRESSED_CEILING,
                "uncapped_priority": round(priority, 2),
                "reason": suppressed,
            }
        else:
            components["suppression_note"] = suppressed
        priority = capped

    priority = max(0.0, min(100.0, priority))
    components["priority"] = round(priority, 2)
    return PriorityResult(round(priority, 2), band_for(priority), components)


#: How evidence strength is decided. Written out so an analyst can check the reasoning
#: rather than trust a word.
EVIDENCE_RULES = {
    "supports_3": "+1 when at least 3 independent supporting records were found",
    "supports_8": "+1 when at least 8 independent supporting records were found",
    "amount_quality": "-1 when a supporting record's amounts do not reconcile with its fee",
    "single_heuristic": "-1 when the finding rests on one heuristic with no corroboration",
    "bands": "high when the total is >= 2, medium at 1, low at 0 or below",
}


def evidence_strength(
    independent_supports: int,
    amount_quality_affected: bool,
    single_heuristic: bool,
    coverage_note: str | None = None,
) -> tuple[str, list[str]]:
    """Return the strength band and the plain reasons that produced it."""
    score = 0
    reasons: list[str] = []

    if independent_supports >= 3:
        score += 1
        reasons.append(f"{independent_supports} independent supporting records.")
    else:
        reasons.append(
            f"Only {independent_supports} supporting record"
            f"{'s' if independent_supports != 1 else ''} found.")
    if independent_supports >= 8:
        score += 1
        reasons.append("Support is repeated well beyond the detector's minimum.")

    if amount_quality_affected:
        score -= 1
        reasons.append(
            "At least one supporting record has an unresolved amount discrepancy, so "
            "amount-derived figures here are weaker than the structure itself.")
    if single_heuristic:
        score -= 1
        reasons.append("The finding rests on a single heuristic with no independent corroboration.")
    if coverage_note:
        reasons.append(coverage_note)

    strength = "high" if score >= 2 else "medium" if score == 1 else "low"
    reasons.append(
        "Evidence strength describes how well supported the observation is. It is not a "
        "probability, and 'high' does not establish ownership or intent.")
    return strength, reasons
