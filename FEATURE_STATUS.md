# Feature status

Honest state of every requirement. **Implemented** means built, exercised by a test or
by the verified end-to-end run, and working. **Partial** means the useful core is there
with a named gap. **Not implemented** means not built — not "mostly done".

Verified by: 71 passing tests, plus a full HTTP walkthrough of upload → analyse → alert →
graph → evidence → export → offline evaluation on the supplied 10,000-row file.

---

## Core workflow

| Capability | Status | Notes |
| --- | --- | --- |
| CSV ingestion (extensionless, UTF-8, BOM, CRLF, quoted cells) | Implemented | Format identified by content |
| Semicolon arrays, order and duplicates preserved | Implemented | Positional index stored per input/output |
| Exact BTC → integer satoshi conversion | Implemented | `Decimal` only; sub-satoshi values rejected |
| Compatibility and strict validation policies | Implemented | Counts reconcile in both; nothing repaired |
| Counts reconcile to input rows | Implemented | Asserted in the importer and in tests |
| Idempotent re-import within a case | Implemented | Identical bytes reuse the dataset |
| Duplicate observation vs duplicate transaction | Implemented | Same payload → extra observation |
| Conflicting payload under one TXID quarantined | Implemented | First record is never overwritten |
| Fee preserved, residual computed independently | Implemented | 262 residuals reported on the sample |
| Label removal at the ingestion boundary | Implemented | Enforced by permutation tests |
| SQLite case store with source-row archive | Implemented | Single serialised writer |
| Background worker with progress polling | Implemented | Cancellable between stages |
| Atomic run writes | Implemented | Failed/cancelled runs delete partial results |
| JSON import adapter | Implemented | Array or `{transactions: [...]}`; not exercised on a real-world file |
| XML import adapter | Implemented | `defusedxml`, external entities disabled |

## Detection

| Capability | Status | Notes |
| --- | --- | --- |
| DET01 CoinJoin-like structure | Implemented | 230/230 labelled addresses recovered |
| DET02 Collection behaviour | Implemented | Graded severity; benign batch and service lookalike tested |
| DET03 Payment participants | Implemented | 40/40 recovered; never inherits collector priority |
| DET04 Peeling sequence | Implemented | 14/14 recovered; broken-chain negative tested |
| DET05 Candidate common control | Implemented | 5/5; collaborative transactions excluded |
| DET06 Shared-IP safeguard | Implemented | 0/6 noise addresses grouped; mutation test passes |
| DET07 High-volume safeguard | Implemented | Exchange not flagged; demotes collection lookalike |
| DET08 General anomaly | Implemented | No category invented |
| Configurable, versioned thresholds | Implemented | Recorded with every run |
| No hardcoded addresses or counts | Implemented | Verified by fixtures with disjoint names |

## Machine learning

| Capability | Status | Notes |
| --- | --- | --- |
| Address-level Isolation Forest | Implemented | 18 features, seed 42, fit 0.57 s |
| Strict feature allowlist | Implemented | Enforced at runtime, raises on violation |
| Preprocessing saved with the model | Implemented | Imputer + quantile transform in the bundle |
| Frozen reference distribution | Implemented | Percentiles with documented tie policy |
| Higher score = more unusual | Implemented | Negated once, in the bundle |
| Trusted-artifact loading only | Implemented | Project directory + integrity digest |
| Model reused across imports | Implemented | Retraining is an explicit command |
| Graceful "ML unavailable" | Implemented | Rules browsable; addresses unscored, not zeroed |
| Entity-disjoint holdout | Implemented | `--holdout-fraction` splits by address |
| Held-out evaluation actually run | **Partial** | Supported and documented, but the reported numbers are transductive — no disjoint dataset exists |
| Calibrated / supervised model | Not implemented | Needs independently labelled data |
| Separate transaction-level model | Not implemented | Transaction findings come from rules |
| Graph embeddings | Not implemented | Out of scope for this release |

## Interface

| Capability | Status | Notes |
| --- | --- | --- |
| Upload and validation screen | Implemented | Policy choice, reconciliation, quality findings |
| Dataset overview screen | Implemented | Coverage, progress, quality impact, per-detector counts |
| Alert inbox | Implemented | Priority sort, faceted filters, stable pagination |
| Investigation workspace | Implemented | Graph centre, evidence right, records/network tabs |
| Typed graph, shapes + labelled edge styles | Implemented | Transaction node always preserved |
| Bounded neighbourhood with explicit expansion | Implemented | Budget enforced; truncation stated |
| Keyboard-accessible table alternative | Implemented | Toggle beside the canvas |
| Colour always paired with text | Implemented | Bands are words, not just colours |
| Loading / empty / failed / insufficient states | Implemented | Failures never show stale or invented results |
| Two clicks from alert to source row | Implemented | Source-row dialog shows the original row |
| Recharts visualisations | **Partial** | Dependency installed; bar rows are hand-rendered CSS. No chart component was needed |

## Explanation and review

| Capability | Status | Notes |
| --- | --- | --- |
| Evidence cards (4 questions) | Implemented | Values inserted from the run only |
| Alternative explanations shown | Implemented | Presented as possibilities, not facts |
| False-positive review surfaces | Implemented | Volume-only, IP-only, collaborative, uncertain roles |
| Data-quality impact | Implemented | Dataset totals and per-alert caveats |
| Review states with actor/reason/history | Implemented | Version-checked against stale writes |
| Notes | Implemented | Stored per alert |
| Analyst decisions never retrain | Implemented | Asserted by test |
| Evidence sensitivity | Implemented | Ranking-only recalculation, explicitly labelled |
| PDF + JSON + CSV export with manifest | Implemented | Hashes, versions, config, limitations |
| Reproducible re-run instructions in export | Implemented | Source hash, mode, versions |

## Evaluation

| Capability | Status | Notes |
| --- | --- | --- |
| Isolated CLI evaluator | Implemented | Only reader of ground truth |
| Predictions frozen before labels | Implemented | Written at run time |
| Precision / recall / FPR with denominators | Implemented | Never a bare rate |
| PR-AUC and Precision@K | Implemented | K = 20, 50, 100 |
| Rules-only vs model-only vs combined | Implemented | All three reported |
| Per-pattern support and recall | Implemented | Each labelled pattern separately |
| Role mapping documented | Implemented | Victim participation kept separate |
| Coverage counted, not dropped | Implemented | Unscored addresses included |
| Evaluation screen in the UI | Not implemented | CLI only, by design for this release |

## Not implemented

Stated plainly rather than implied:

- **GeoIP / ASN enrichment** — no licensed local database is bundled.
- **Timeline replay** — the workspace shows a time-ordered record table, but there is no
  play/pause scrubber.
- **Saved views and two-run comparison** — runs are immutable and listed, but there is
  no diff view.
- **Cluster splitting and per-edge rejection** — a cluster hypothesis can be accepted or
  rejected as a whole with a note; finer editing is not built.
- **Watchlist exposure paths** — no watchlist import.
- **Feedback queues feeding a future training set.**
- **Multi-user authentication and roles** — single trusted local operator only.
- **Incremental imports and checkpoint resume** — a run restarts from the beginning.
- **Backup/restore UI** — documented as a stopped-service file copy.
- **100,000-row scale profiling** — only the 10,000-row file has been measured.

## Known gaps in what *is* built

- Precision is **0.7073** against a 0.85 target. Not tightened, because tuning against
  the answer key would make the number meaningless.
- Reported metrics are **transductive** (fit and scored on the same population).
- Verified on **Windows 11**; the offline Linux target has not been tested directly.
- The JSON and XML adapters are tested with synthetic documents only.
- `common_control` produced 12 false positives among 17 findings on the sample — they
  carry low evidence strength and medium priority, which is the honest presentation, but
  the rule is weak at `min_shared_transactions = 2`.
