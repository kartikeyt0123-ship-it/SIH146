"""Deterministic explanation templates.

Every sentence an analyst reads is assembled here from values the detector actually
measured. Templates insert computed numbers and nothing else: there is no free text, no
generated prose and no fixed demo wording. If a value was not measured, the sentence
that would have used it is not produced at all.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

SATS = Decimal(10) ** 8


def btc(sats: int | float | None) -> str:
    """Render satoshis as BTC for display, keeping the exact satoshi figure available."""
    if sats is None:
        return "an unrecorded amount"
    return f"{Decimal(int(sats)) / SATS:.8f} BTC"


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _coinjoin(ind: dict[str, Any]) -> str:
    parts = [
        f"This transaction has {_plural(ind['input_count'], 'input')} and "
        f"{_plural(ind['output_count'], 'output')}, of which "
        f"{ind['equal_output_count']} share a single denomination of "
        f"{btc(ind.get('denomination_sats'))} "
        f"(within a {ind['equal_amount_band_sats']} satoshi band)."
    ]
    share = ind.get("equal_output_share")
    if share is not None:
        parts.append(f"That is {share * 100:.0f}% of the outputs at one value.")
    parts.append(
        "Repeated equal-value outputs across many inputs are the defining shape of a "
        "collaborative (CoinJoin-like) transaction. The structure is reported; no "
        "conclusion about mixing intent or criminality follows from it.")
    return " ".join(parts)


def _collection(ind: dict[str, Any]) -> str:
    parts = [
        f"{_plural(ind['distinct_payers'], 'distinct payer address', 'distinct payer addresses')} "
        f"paid this address across {_plural(ind['contributing_transactions'], 'transaction')} "
        f"within a {ind['window_hours']:.0f}-hour window "
        f"(observed span {ind['observed_span_hours']:.1f} hours), "
        f"totalling {btc(ind['incoming_sats_in_window'])}."
    ]
    if ind.get("payments_similar_in_size"):
        parts.append(
            f"The payments were similar in size (coefficient of variation "
            f"{ind['payment_amount_cv']:.2f}), which is consistent with a common "
            f"demanded amount but equally with a fixed price or ticket.")
    else:
        parts.append(
            f"Payment sizes varied (coefficient of variation {ind['payment_amount_cv']:.2f}).")
    if ind.get("subsequent_outflow_transactions"):
        parts.append(
            f"The address later spent across "
            f"{_plural(ind['subsequent_outflow_transactions'], 'transaction')} "
            f"to {_plural(ind['distinct_outflow_destinations'], 'distinct destination')}, "
            f"moving {btc(ind['subsequent_outflow_sats'])}.")
    else:
        parts.append("No subsequent outflow from this address appears in the imported period.")
    parts.append(
        f"The detector's threshold was {ind['min_distinct_payers_threshold']} distinct "
        f"payers in the window.")
    return " ".join(parts)


def _participant(ind: dict[str, Any]) -> str:
    return (
        f"This address paid {btc(ind['paid_sats'])} across "
        f"{_plural(ind['payments_to_collection_point'], 'transaction')} to "
        f"{ind['collection_point']}, an address showing a collection pattern between "
        f"{ind['collection_window_start']} and {ind['collection_window_end']}. "
        f"The role recorded is participation in that payment flow. In the behaviour this "
        f"pattern models the paying party is commonly the injured party, so this address "
        f"does not inherit the collection point's review priority.")


def _peeling(ind: dict[str, Any]) -> str:
    shares = ind.get("dominant_output_shares", [])
    share_text = (f"{min(shares) * 100:.0f}%-{max(shares) * 100:.0f}%"
                  if shares else "an unrecorded share")
    gaps = ind.get("hop_gaps_hours", [])
    gap_text = (f"{min(gaps):.1f}-{max(gaps):.1f} hours" if gaps else "unrecorded intervals")
    return (
        f"A sequence of {_plural(ind['hop_count'], 'transaction')} was found in which the "
        f"largest output carries {share_text} of each transaction's output value forward, "
        f"while smaller amounts branch away to "
        f"{_plural(ind['side_recipient_count'], 'side recipient')}. "
        f"Consecutive hops are {gap_text} apart, within the "
        f"{ind['max_gap_hours_threshold']:.0f}-hour maximum. "
        f"{btc(ind['entry_value_sats'])} entered the sequence and "
        f"{btc(ind['total_peeled_off_sats'])} was peeled off along it, leaving "
        f"{btc(ind['final_continuation_sats'])} at the last observed hop. "
        f"Continuity is inferred from address reuse and timing; the records carry no "
        f"previous-output references, so this is a candidate sequence rather than "
        f"verified coin movement.")


def _common_control(ind: dict[str, Any]) -> str:
    text = (
        f"This address was spent together with "
        f"{_plural(ind['member_count'] - 1, 'other address', 'other addresses')} across "
        f"{_plural(ind['supporting_transaction_count'], 'transaction')}, producing "
        f"{_plural(ind['inferred_edge_count'], 'inferred association')} under rule "
        f"{ind['rule_version']}. The rule requires a co-spend to repeat at least "
        f"{ind['min_shared_transactions_threshold']} times before an association is drawn.")
    if ind.get("excluded_transactions"):
        text += (
            f" {_plural(ind['excluded_transactions'], 'transaction')} involving these "
            f"addresses were excluded as possibly collaborative, where the "
            f"common-input assumption does not hold.")
    text += (
        " Common control is a hypothesis about spending behaviour. It is not verified "
        "ownership, and no network observation contributed to it.")
    return text


def _shared_ip(ind: dict[str, Any]) -> str:
    text = (
        f"{_plural(ind['distinct_addresses_observed'], 'address', 'addresses')} were "
        f"observed at endpoint {ind['endpoint']} across "
        f"{_plural(ind['observation_count'], 'record')}.")
    if ind.get("looks_like_shared_infrastructure"):
        text += (
            " The number of addresses at this endpoint is more consistent with shared "
            "infrastructure - a gateway, VPN exit or relaying node - than with one actor.")
    text += (
        " These addresses have NOT been grouped, and no review priority has been "
        "transferred between them. A network sighting records where a record was seen, "
        "not where a transaction originated or who controls an address.")
    return text


def _high_volume(ind: dict[str, Any]) -> str:
    text = (
        f"This address appears in {_plural(ind['transaction_count'], 'transaction')} with "
        f"{_plural(ind['distinct_counterparties'], 'distinct counterparty', 'distinct counterparties')} "
        f"over {ind['activity_span_hours'] / 24:.1f} days, placing it above the case's own "
        f"{ind['case_activity_percentile_cut']}-transaction activity cut.")
    if ind.get("bidirectional_flow"):
        text += (
            f" Value flows both ways: {ind['distinct_senders']} distinct senders and "
            f"{ind['distinct_receivers']} distinct receivers, moving "
            f"{btc(ind['received_sats'])} in and {btc(ind['spent_sats'])} out "
            f"(flow balance {ind['flow_balance']:.2f}).")
    if ind.get("service_like_profile"):
        text += (
            f" Value arrives from many parties and is paid back out to many "
            f"({ind['distinct_receivers']} distinct receivers, against a threshold of "
            f"{ind['min_outbound_counterparties']}). That pass-through shape is the "
            f"profile of a service such as an exchange or payment processor, and is "
            f"what distinguishes it from a collection point, which takes in from many "
            f"but sends onward to few.")
    text += (
        " Volume is treated as context only: it contributes no rule severity and cannot "
        "on its own produce a high-priority lead or any accusation.")
    return text


def _anomaly(ind: dict[str, Any]) -> str:
    return (
        f"The model places this address at the {ind['anomaly_percentile']:.1f}th percentile "
        f"of unusualness against the frozen reference population, above the "
        f"{ind['report_above_percentile']:.0f}th percentile reporting threshold, across "
        f"{_plural(ind['transaction_count'], 'transaction')}. No named behavioural "
        f"pattern was matched, so the deviation is reported without assigning a category. "
        f"The percentile measures how unusual the behaviour looks in this dataset; it is "
        f"not a probability of wrongdoing.")


_TEMPLATES = {
    "coinjoin_like": _coinjoin,
    "collection_pattern": _collection,
    "payment_participant": _participant,
    "peeling_sequence": _peeling,
    "common_control": _common_control,
    "shared_ip_observation": _shared_ip,
    "high_activity_context": _high_volume,
    "statistical_anomaly": _anomaly,
}


def render_explanation(detector: str, indicators: dict[str, Any]) -> str:
    """Render the explanation for a finding, or raise if the detector has no template."""
    template = _TEMPLATES.get(detector)
    if template is None:
        raise KeyError(f"no explanation template for detector {detector!r}")
    try:
        return template(indicators)
    except (KeyError, TypeError) as exc:
        # A template must never invent a value it was not given.
        raise KeyError(
            f"detector {detector!r} did not supply an indicator its explanation needs: {exc}"
        ) from exc
