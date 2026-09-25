# Demonstration script

About 8 minutes, fully offline. The point to land is not "we detect ransomware" — it is
**every finding is traceable, and the system knows what it cannot prove.**

## Before you start

```bash
cd backend && .venv/bin/python -m chainlens          # Windows: .venv\Scripts\python
```

Open http://127.0.0.1:8000. Disconnect the network first — the demo is stronger when the
machine is visibly offline. Have a terminal ready for the evaluation step.

Pre-flight: model trained (`/api/health` shows `"status": "available"`), frontend built,
and a fresh case ready to import into.

---

## 1 · Import and label isolation (90 s)

Create a case, drag in `data/samples/ps3_transactions` — **note it has no file
extension**; the format is identified by content.

Choose **Synthetic compatibility** and import.

Say, pointing at the result panel:

> 10,000 rows in: 9,738 accepted clean, 262 with warnings, 0 quarantined. The three
> numbers reconcile to the input count — nothing is silently dropped.

Then the amber callout:

> The file ships with an answer column, `is_planted_suspicious`. It was removed at the
> ingestion boundary, before any analysable record existed. It survives only in the
> restricted archive of the original upload. Nothing downstream can reach it.

Point at the quality table:

> 262 rows have amounts that don't reconcile with their stated fee. That's a data-quality
> finding, not a behavioural one. A fee residual never makes an address suspicious.

*Optional, 15 s:* re-import the same file — it is reused, not duplicated.

## 2 · Run the analysis (45 s)

Click **Run analysis**. Stages advance: graph, features, model, behavioural rules,
scoring, alerts.

> Roughly three seconds for 10,000 rows on this laptop. Structural detection runs first,
> because its output — which transactions are collaborative — is what the ownership
> heuristics must exclude.

On completion, the per-detector bar chart appears.

## 3 · Follow a collection lead (2 min)

Open the alert inbox. Sort is by review priority.

Open the top **Collection pattern** alert (priority ≈ 87, evidence **high**).

Walk the card in order:

> **What happened** — 25 distinct payer addresses paid this address inside a 72-hour
> window, then it moved funds on to just 2 destinations.
>
> **Why flagged** — two bars: rule severity and anomaly percentile, with the formula
> written underneath. Severity is graded, and the breakdown shows exactly how it was
> reached: 60 for the payer count, +10 because the money moved on to only two places.
>
> **Which records** — every supporting transaction. Click **Source** on any row.

Open a source row. The label column is highlighted amber:

> This is the original row, answer column and all. The analyst can see it; the detectors
> and the model never could.

Then the fourth question:

> **What remains uncertain** — a merchant, a payment processor and a donation address all
> produce this shape. The card says so itself, before anyone asks.

## 4 · The peel chain and inferred continuity (1 min)

Open the top **Peeling sequence** alert (priority ≈ 89).

> Seven transactions where the largest output carries 99–100% of the value forward while
> small amounts branch away, hops 5 to 18 hours apart.

Click **Open in graph**.

> Addresses are circles, transactions are rectangles, endpoints are diamonds — shape, not
> just colour. Solid edges are relationships supplied by the data. Dashed edges are
> inferred.
>
> There is no direct address-to-address payment line anywhere, because the data cannot
> support one. The transaction node always stays in between. And the continuity is
> dashed because the file has no previous-output references — this is a candidate
> sequence, not proven coin movement.

Point at the node budget:

> The full graph is never drawn. This is a bounded neighbourhood; expansion is explicit.

## 5 · The safeguards — the part that matters (2 min)

This is the differentiator. Filter the inbox to **high activity**.

> One address, 400 transactions — far more than anything else in the dataset. It is
> **medium** priority, described as "high activity requiring context".
>
> It looks exactly like a collection point: 200 distinct payers converging on it. But
> value also flows *out* to 200 distinct receivers. Money from many going to many is a
> service. Money from many going to few is a collection. So the collection finding was
> demoted before it was ever written, and the card explains why.

Then filter to **Shared network observation**:

> Several addresses recur at one endpoint. The system shows the association and states
> plainly that these addresses have **not** been grouped and no priority has been
> transferred between them. A shared IP can never create an ownership edge — a NAT
> gateway or a relaying node puts unrelated people behind one address.

Optionally open a **CoinJoin-like** alert:

> Equal-value outputs across many inputs. Flagged as a structure, not as laundering — and
> these transactions are excluded from common-input ownership merging, because that is
> precisely where the heuristic is wrong.

## 6 · Test the ranking, then export (1 min)

On any alert, expand **Evidence sensitivity** and click *Without the model score*.

> Before and after priority, side by side. And it says what it did: the ranking was
> recalculated, the model was not rescored, the stored alert is unchanged. It would be
> easy to call this "retraining". It isn't, so we don't.

Mark the alert **relevant** with a reason, add a note, then **Export this finding**.

> PDF summary, JSON findings, CSV evidence, and a manifest with the source SHA-256,
> detector version, model version and configuration. It lets a recipient detect a change.
> It is not a legal certification, and the package says so in its own limitations file.

## 7 · Evaluate — separately (1 min)

In the terminal:

```bash
python -m chainlens.evaluation.evaluate \
  --predictions ../chainlens_data/predictions/<run_id>.json \
  --ground-truth ../data/samples/ps4_ground_truth
```

> This is the only component allowed to open a ground-truth file, and it reads
> predictions frozen before any label was available.
>
> Recall 1.00, precision 0.71, false-positive rate 0.041. Every labelled pattern fully
> recovered. The exchange: not flagged. The six innocent shared-IP addresses: not
> flagged.
>
> Precision is below our 0.85 target. We could have moved thresholds until it cleared —
> but we would have been tuning against the answer key, and the number would mean
> nothing. So it stands as measured.
>
> And these numbers are exploratory: the model was fitted and scored on the same
> population. That is written on the model manifest, not buried.

## Closing line

> Anyone can produce a list of suspicious addresses. This tells you why, shows you the
> row it came from, offers the innocent explanation alongside the suspicious one, and
> refuses to promote a lead it can't support.

---

## If something fails

- **Page won't load** → the backend is not running; `python -m chainlens`.
- **Model unavailable** → rules still run and alerts show as unscored rather than
  normal. Train with `python -m chainlens.ml.train --input data/samples/ps3_transactions`.
- **Run fails** → the failure is shown as a failure. No previous run is substituted. Say
  so and start a new run; that behaviour is deliberate and worth pointing out.

## Fallback

If the live demo cannot run, `python -m pytest` (71 tests, ~2.5 min) demonstrates the
same guarantees — particularly `test_leakage.py`, where permuting, removing and inverting
the answer column each leave predictions byte-identical.
