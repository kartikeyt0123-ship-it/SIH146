# Detectors: thresholds and reasoning

Version `detectors-2026.09.1`. Defaults live in `backend/chainlens/detectors/config.py`,
are overridable per run via `POST /cases/{id}/runs`, and are recorded with every run and
every export.

**How these numbers were chosen.** From published descriptions of each behaviour and from
the shape of the population being analysed — not by fitting against the supplied answer
key. Where a threshold is compared against the dataset's own distribution (the
high-volume percentile), that is stated, so the rule does not depend on a hardcoded idea
of "busy".

---

## DET01 · CoinJoin-like structure

`min_inputs 3` · `min_outputs 3` · `min_equal_outputs 3` · `equal_amount_band_sats 1`

A CoinJoin is defined by repeated equal-value outputs across many inputs. Three is the
textbook minimum — below it, equal values are coincidence. The one-satoshi band absorbs
rounding without merging genuinely different denominations.

On the supplied sample no 3-output ordinary transaction repeats an output value at all,
so this threshold is not a compromise.

**Restraint.** Reports a *structure*. Says nothing about mixing intent or legality, and
offers "a batched payout produces the same shape" as an alternative. Crucially, tagged
transactions are excluded from common-input ownership merging — a collaborative
transaction is exactly where "shared inputs means shared owner" is wrong.

---

## DET02 · Collection behaviour

`min_distinct_payers 5` · `window_hours 72` · `min_report_severity 40`
`min_transactions_for_corroboration 5` · `onward_movement_ratio 0.5`
`concentrated_outflow_destinations 3` · `similar_amount_cv 0.5`

The payer floor is the starting hypothesis. **It is not the decision.** On the supplied
population, 21% of addresses receive from ≥5 distinct payers within 72 hours — a flat
threshold produced 579 alerts, which is not an inbox anyone can work.

Severity is graded instead:

```
base = clamp(20 × log2(payers / floor) + 20, 0, 60)
```

Saturating, because "many payers" stops being more informative once it is clearly many.
Then corroboration: +8 similar payment sizes, +7 arrival burst, +10 most of the value
moving on to ≤3 destinations, +5 high inbound concentration. Capped at 75.

**Corroboration is only credited above 5 contributing transactions.** On a quiet address,
"all the value arrived in this window" and "it was spent on to one place" are true almost
by default; crediting them would promote ordinary low-activity addresses. This single
condition is what took false positives from 363 to 120.

Candidates that clear the payer floor but not the evidence bar are **counted in the run
statistics**, not hidden — on the sample, 574 of 579.

**Restraint.** Always worded "collection pattern". A merchant, a payment processor and a
donation address are listed as alternatives on every card. An address with a service-like
profile has its severity capped at 30, with the reason written into the breakdown.

---

## DET03 · Payment participants

Severity `20`.

Every payer of a reported collection point, taken from *all* payments to it rather than
only the busiest window — the window decides whether the address is a collection point;
once it is, a payer who paid a day outside it is just as much a participant.

**Restraint.** A participant **never** inherits the collection point's priority. In the
behaviour this models, the paying party is commonly the injured party, and the card says
so in those words.

---

## DET04 · Peeling sequence

`min_chain_length 3` · `min_dominant_share 0.80` · `max_gap_hours 72`
Continuation severity `75`, side recipient `40`.

A dominant output carrying most of the value forward while small amounts branch away.
Three hops is the shortest run that is a pattern rather than ordinary change handling;
80% distinguishes a peel from a routine two-output spend.

Every address in the sequence is reported in its own right, with its role. A continuation
address carries the balance forward; a side recipient received one branching amount and
may have no further connection — so they are scored differently.

**Restraint.** The supplied contract has no previous-output references, so continuity is
inferred from address reuse and timing. Every card says this. Incomplete chains are
expected: hops before the first or after the last imported transaction are invisible.

---

## DET05 · Candidate common control

`min_shared_transactions 2` · `collaborative_input_threshold 5` · `max_cluster_size 200`

The common-input heuristic, with both of its well-known failure modes handled: a single
co-spend is not enough, and collaborative transactions are excluded entirely.

Every inferred edge records its rule version, supporting TXIDs and the transactions that
were deliberately excluded, so the analyst can see what the rule declined to count.

**Restraint.** A hypothesis about spending behaviour, not verified ownership. **A shared
IP can never create an edge here** — network association is a separate signal and stays
separate. Clusters are reversible: an analyst can reject one with a reason, and the raw
observations are untouched.

**Known weakness.** `min_shared_transactions = 2` is permissive: 12 of 17 findings on the
sample were false positives. They carry low evidence strength and medium priority, which
is the honest presentation, but the rule would benefit from more corroboration.

---

## DET06 · Shared-IP safeguard

`min_observations 3` · `min_observations_per_address 2` · `min_recurring_addresses 3`
Severity **0**.

Counting every address seen at an endpoint is meaningless — a single transaction already
contributes three to six. The signal is addresses that **recur** at an endpoint. This
also keeps a busy service's endpoint from firing: its counterparties appear once each.

**Restraint.** This detector exists to show the analyst an association *and tell them what
it does not mean*. It contributes zero severity, creates no ownership edge, and its card
states that the addresses have not been grouped and no priority has been transferred. A
NAT gateway, VPN exit or relaying node puts unrelated people behind one address.

---

## DET07 · High-volume safeguard

`activity_percentile 99` · `min_transactions 50` · `min_inbound_counterparties 20`
`min_outbound_counterparties 20` · Severity **0**.

Volume is compared against the case's own population, so the rule adapts instead of
assuming what "busy" means.

**The service test is where the money goes, not where it comes from.** A collection point
and an exchange both take value in from many payers. Only a service pays it back out to
many distinct parties; a collection point sends onward to a handful. Requiring diversity
on *both* sides separates them.

This was rebuilt during development. An earlier version used counterparty reciprocity —
counterparties appearing on both sides — and failed: the sample's exchange has 200
senders and 200 receivers with **zero** overlap, so reciprocity was 0.0 and the safeguard
did not fire, leaving the exchange as the top high-priority lead. Outbound diversity is
the correct discriminator.

**Restraint.** High activity produces **no** rule severity, a maximum band of medium, and
the wording "high activity requiring context". Identifying the address as a specific
business would need external information the product does not have.

---

## DET08 · General anomaly

`report_above_percentile 99`.

Model-ranked deviations the rules did not explain. Addresses already covered by a
structural detector are skipped, so this does not duplicate.

**Restraint.** No category is invented. The card states that the percentile measures how
unusual the behaviour looks in this dataset and is not a probability of wrongdoing.
"No supported pattern" and "insufficient evidence" are valid outcomes — and no alert
never certifies legitimacy.

---

## Tuning without fooling yourself

Thresholds are meant to be changed. Two rules:

1. **Never tune against `ps4_ground_truth`.** Numbers produced that way describe the
   answer key, not the detector. Use `backend/tests/fixtures.py` — hand-written
   scenarios with entity names disjoint from the sample.
2. **Changing a threshold creates a new run.** Existing alerts are immutable; an old card
   is never rewritten under a new configuration.

Each detector has a positive fixture, a negative fixture and a legitimate lookalike in
`tests/test_detectors.py`. A new threshold should be justified by those, and by the
distribution of the data being analysed — not by a metric moving in the right direction.
