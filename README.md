# ChainLens

An offline Bitcoin investigation platform. It imports transaction and network metadata,
identifies behavioural patterns, and presents prioritised investigative leads with
evidence an analyst can open, question and export.

It is built around one idea: **a finding is only useful if you can see what produced it.**
Every alert states what was observed, why it was flagged, which source rows support it,
and what remains uncertain — and every number on the card comes from the run that
produced it, not from a template.

---

## What it does

The core journey is `upload → validate → analyse → review alerts → explore graph →
inspect evidence → annotate → export`.

- **Imports** semicolon-array CSV (JSON and XML adapters included), identifying files by
  content so extensionless files work. Handles UTF-8, BOMs, quoted cells and CRLF.
- **Validates** under two policies, reporting accepted / warning / quarantined counts
  that always reconcile to the input row count. Nothing is silently repaired.
- **Removes evaluation labels** at the ingestion boundary, before a canonical record
  exists. This is enforced by tests, not just asserted.
- **Detects** eight behaviours (below), combining explicit graph/behavioural rules with
  an address-level Isolation Forest.
- **Explains** each finding with a deterministic template filled from measured values.
- **Exports** a PDF summary, JSON manifest and CSV evidence with the dataset hash and
  every version needed to reproduce the run.
- **Evaluates** separately, in a command-line tool that is the only component ever
  permitted to read a ground-truth file.

---

## Quick start

Requires Python 3.12+ and Node 20+ (Node is needed to *build* the frontend, not to run
the application).

```bash
# 1. Backend
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt        # Windows: .venv\Scripts\pip

# 2. Train the model once (no pretrained classifier is assumed)
.venv/bin/python -m chainlens.ml.train --input ../data/samples/ps3_transactions

# 3. Build the frontend
cd ../frontend && npm install && npm run build

# 4. Start — this single process serves the API and the compiled frontend
cd ../backend && .venv/bin/python -m chainlens
```

Then open **http://127.0.0.1:8000**.

For frontend development, run `npm run dev` (port 5173, proxies `/api` to port 8000)
alongside `python -m chainlens`.

### Commands

| Command | Purpose |
| --- | --- |
| `python -m chainlens` | Start the application on localhost |
| `python -m chainlens.ml.train --input FILE` | Fit and freeze a model bundle |
| `python -m chainlens.evaluation.evaluate --predictions F --ground-truth F` | Offline evaluation |
| `python -m pytest` | Run the test suite |

`--holdout-fraction 0.3` on `train` splits by **address**, so no address contributes to
both the fitted preprocessing and the held-out measurement.

---

## Architecture

One Python process, one SQLite file, one compiled frontend. No broker, no external
services, no hosted model.

```
frontend/  React + TypeScript + Vite, Cytoscape.js graph
           served by FastAPI in the packaged build

backend/chainlens/
  ingest/      sniffing, normalisation, validation policies   ← LABEL BOUNDARY
  graphx/      analysis view + typed graph construction
  features/    address feature extraction (strict allowlist)
  ml/          Isolation Forest bundle: train, freeze, infer
  detectors/   the eight pattern modules + versioned thresholds
  scoring/     priority policy + deterministic explanations
  runner/      pipeline orchestration and the local worker thread
  exporting/   PDF / JSON / CSV evidence packages
  evaluation/  offline evaluator (the only reader of ground truth)
  api/         HTTP routes
```

**The label boundary.** `ingest/` strips any label column before a canonical record is
created. The raw upload is archived byte-for-byte in a restricted table; everything
downstream reads only the sanitised tables, which have no label column by construction.

**Concurrency.** CPU-heavy analysis runs on a background worker thread with progress
polled over HTTP. All writes pass through a single process-wide lock and an IMMEDIATE
transaction, so SQLite never sees concurrent writers. Results are written in one
transaction at the end — a partial run can never appear on screen as a finished one.

---

## The three scores, kept separate

Collapsing these is how a ranking starts to look like a verdict.

| Concept | What it is | What it is not |
| --- | --- | --- |
| **Anomaly percentile** | Rank against a frozen reference population | A probability |
| **Review priority** | `0.60 × percentile + 0.40 × max(rule severity)`, 0–100 | A probability of wrongdoing |
| **Evidence strength** | low / medium / high from support and coverage | Proof of ownership or intent |

Bands: high ≥ 75, medium ≥ 40, low < 40. Rules combine by **maximum, never sum**.
An address with no model score is **unscored**, never scored as zero — its priority is
the rule severity alone and the alert says so.

Safeguard findings (high activity, shared endpoint) carry **zero** rule severity and are
capped at 60, so they can never enter the high band on an anomaly measurement alone.

---

## The eight detectors

| # | Detector | What it reports | Built-in restraint |
| --- | --- | --- | --- |
| 1 | CoinJoin-like structure | ≥3 in, ≥3 out, ≥3 equal-value outputs | Excluded from ownership merging; no mixing intent claimed |
| 2 | Collection behaviour | Many distinct payers converging in a window | Graded severity, not payer count alone; service profile demotes it |
| 3 | Payment participants | Upstream payers of a collection event | Never inherits the collector's priority |
| 4 | Peeling sequence | Dominant output carried forward, small amounts branching | Labelled inferred continuity — no prevouts exist |
| 5 | Candidate common control | Repeated co-spending | Collaborative transactions excluded; a shared IP can never create an edge |
| 6 | Shared-IP safeguard | Addresses recurring at one endpoint | Zero severity; explicitly does **not** group them |
| 7 | High-volume safeguard | Busy addresses needing context | Volume alone cannot produce a high-priority lead |
| 8 | General anomaly | Unusual behaviour with no named pattern | No category is invented |

All thresholds live in `detectors/config.py`, are versioned (`detectors-2026.09.1`) and
are recorded with every run. They were chosen from published descriptions of each
behaviour, not fitted against the supplied answer key.

**Why collection severity is graded.** On this dataset 21% of addresses receive from ≥5
distinct payers within 72 hours, so a flat threshold produced 579 alerts. Severity is now
graded by how far the evidence exceeds the floor, plus corroboration (amount similarity,
burst, concentrated onward movement) — and corroboration is only credited when there is
enough activity for it to mean anything, because "all value arrived in this window" is
near-automatic for a quiet address. 5 alerts are raised; the other 574 candidates are
counted in the run statistics rather than hidden.

---

## Measured results

Measured on this machine (Windows 11, Python 3.12.10), not projected. Reproduce with the
commands above.

**Import** — supplied 10,000-row sample, both policies reconcile exactly:

| Mode | Accepted | With warnings | Quarantined | Total |
| --- | --- | --- | --- | --- |
| Compatibility | 9,738 | 262 | 0 | 10,000 |
| Strict | 9,738 | 247 | 15 | 10,000 |

262 rows have amounts that do not reconcile with the supplied fee (247 within one
satoshi, 15 above; largest 0.00608103 BTC). These are data-quality findings and never
contribute to a suspicious-pattern score.

**Performance** — 10,000 rows, 3,197 addresses: analysis **3.1 s** end to end
(target was under 120 s). Model fit 0.57 s. Test suite **71 passed**.

**Detection**, from the isolated evaluator against `ps4_ground_truth`:

| Metric | Value | Denominator |
| --- | --- | --- |
| Precision | 0.7073 | 410 predicted positive |
| Recall | 1.0000 | 290 labelled positive |
| False-positive rate | 0.0413 | 2,907 labelled negative |
| F1 | 0.8286 | — |

Per ground-truth pattern (recall): coinjoin_mixing 230/230, ransomware_victim_payment
40/40, peel_chain_hop 14/14, same_actor_cluster 5/5, ransomware_collector 1/1.

**The safeguards hold.** `legit_exchange_high_volume` (400 transactions) was **not**
flagged — it is reported as "high activity requiring context" at medium priority, and its
collection-shaped inbound traffic was demoted because value also flows out to 200 distinct
receivers. All 6 `shared_ip_innocent_noise` addresses were **not** grouped and **not**
flagged.

**Rules vs model vs combined** (PR-AUC / P@20):

| Variant | PR-AUC | P@20 |
| --- | --- | --- |
| Rules only | 0.6787 | 0.50 |
| Model only | 0.8421 | 0.95 |
| Combined | 0.8037 | 0.90 |

### Against the proposed targets

| Target | Result |
| --- | --- |
| Precision ≥ 0.85 | **Missed — 0.7073** |
| Recall ≥ 0.80 | Met — 1.0000 |
| False-positive rate ≤ 0.05 | Met — 0.0413 |

Precision is below target. The residual false positives are 120 `normal` addresses,
mostly peel-sequence side recipients and payers of collection points that the detector
reported but the answer key labels normal. Raising precision by tightening thresholds
until the numbers look better would be fitting to the answer key, so it has not been done.

**These numbers are exploratory.** The model was fitted and scored on the same
population, which is transductive. They describe this dataset; they are not a measurement
of performance on unseen data. The generator referenced by the supplied notes was not
provided, so `backend/tests/fixtures.py` is a documented hand-written replacement with
entity names disjoint from the sample.

---

## Leakage prevention

The central claim is that findings are earned from behaviour. It is tested three ways:
**permuting**, **removing** and **inverting** the `is_planted_suspicious` column each
produce byte-identical predictions (`tests/test_leakage.py`).

- `is_planted_suspicious`, `is_suspicious`, `pattern_type` and similar names are stripped
  at ingestion and exist only in the restricted source archive.
- The model receives exactly the 18 features in `FEATURE_ORDER`, enforced at runtime by
  `assert_no_identity_leak`, which raises if any other column appears.
- Addresses, TXIDs, IPs and script types are **never** encoded as features — not as
  strings, hashes or category codes.
- The fee-residual magnitude is excluded: it is an artefact of how the file was
  generated, not behaviour, and would be a shortcut to the planted rows.
- No address or expected count is hardcoded anywhere in the detectors.

Ground truth is joined to frozen predictions only by `chainlens.evaluation.evaluate`,
which reads a prediction file written before any label was available and never writes to
the case database.

---

## Offline operation

The application makes **no outbound network requests**. FastAPI's documentation UI is
disabled by default because it loads assets from a CDN; enable it for development with
`CHAINLENS_ENABLE_DOCS=1`. Fonts are system stacks and icons ship in the bundle.

Binds to `127.0.0.1` by default. This build is for **one trusted local operator** and has
no authentication — multi-user deployment would need access controls first.

To prepare an air-gapped machine, vendor the wheels and the npm packages beforehand:

```bash
pip download -r backend/requirements.txt -d vendor/wheels
pip install --no-index --find-links vendor/wheels -r backend/requirements.txt
```

**Verified on Windows 11.** The Linux instructions above are the same commands with
POSIX paths; the target platform has not been tested directly, which is stated here
rather than claimed.

---

## Limitations

These are properties of the data contract, not defects to be worked around:

- **No previous-output references.** Exact UTXO tracing is impossible. Continuity
  between transactions is inferred from address reuse and timing, and is labelled as
  inferred everywhere it appears.
- **No virtual size or block height.** Fee rates cannot be computed and are not shown.
- **An IP observation is not origin or ownership.** It records where a record was seen.
- **Clustering is a hypothesis.** Repeated co-spending suggests possible common control;
  it is not verified ownership, and collaborative transactions are excluded from it.
- **Addresses in the sample are synthetic** and do not verify as real-chain addresses.
  They are treated as opaque identifiers in compatibility mode.
- **No alert does not certify legitimacy.** "No supported pattern" means exactly that.
- **Roles are positions, not conduct.** A payment participant is commonly the injured
  party.
- **The export manifest detects change.** It is not a legal certification, and the audit
  log is append-only at application level — not tamper-proof against someone with
  filesystem access.

Out of scope by design: private keys, signing, fund transfer, wallet connection, live
traffic interception, chain synchronisation and any automatic attribution of identity.

---

## Documentation

- [`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md) — input contract and issue codes
- [`docs/DETECTORS.md`](docs/DETECTORS.md) — thresholds and the reasoning behind them
- [`DEMO.md`](DEMO.md) — the demonstration script
- [`FEATURE_STATUS.md`](FEATURE_STATUS.md) — honest implemented / partial / not-built list
- `docs/evaluation_report.json` — the full machine-readable evaluation
