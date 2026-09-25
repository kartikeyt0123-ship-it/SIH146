# Data dictionary and input contract

## Transaction file

CSV, JSON or XML. The format is detected from content, so files without an extension
work. Encoding is UTF-8 (BOM tolerated); CRLF and LF both accepted; quoted cells handled.

| Field | Required | Interpretation |
| --- | --- | --- |
| `timestamp` | yes | Timezone-aware UTC observation time. A value with no offset is read as UTC. This is when the record was observed, not a verified transaction creation time. |
| `txid` | yes | Stable identifier. 64 hex characters expected; other values are accepted as opaque identifiers in compatibility mode and quarantined in strict mode. |
| `input_addresses` | yes | Semicolon-separated. Order and duplicate occurrences preserved. |
| `output_addresses` | yes | Semicolon-separated. Order and duplicate occurrences preserved. |
| `input_amounts` | yes | Semicolon-separated BTC decimals, aligned by index with `input_addresses`. |
| `output_amounts` | yes | Semicolon-separated BTC decimals, aligned by index with `output_addresses`. |
| `fee` | no | BTC decimal. Stored exactly as supplied; the residual is computed independently. |
| `src_ip`, `dst_ip` | no | IPv4 or IPv6. Endpoint roles retained. Same IP does not imply same owner. |
| `src_port`, `dst_port` | no | Integer 0–65535. Port 8333 is **not** required. |
| `script_type` | no | Preserved as supplied. Unknown values are kept with a warning. |
| `is_planted_suspicious` | — | **Forbidden as input.** Removed at the ingestion boundary. |

### Amounts

BTC decimals are converted to integer satoshis through `Decimal` — never through binary
floating point. A value with sub-satoshi precision is rejected rather than rounded.

```
1.21885469 BTC  →  121885469 sats
```

### The conservation residual

`residual = Σ inputs − Σ outputs − fee`

The supplied fee is never altered. The residual is recorded on every transaction, even
when zero, and even when within tolerance. Default tolerance is 1 satoshi, which exists
solely to absorb the rounding artefacts present in the supplied sample.

A residual is a **data-quality finding**. It is excluded from the model's features and
can never contribute to a suspicious-pattern score.

### Label columns

Any column named `is_planted_suspicious`, `is_suspicious`, `pattern_type`,
`ground_truth`, `label`, `planted` or `wallet_id_label` is removed before a canonical
record is created. Removal is recorded once at dataset level. The original row survives
verbatim in the restricted `source_rows` archive, viewable by an analyst but unreachable
by any detector, the feature pipeline or the model.

---

## Ground-truth file (evaluation only)

| Field | Meaning |
| --- | --- |
| `wallet_id` | An **address identifier**. Not a verified wallet, and not a person. |
| `is_suspicious` | Binary benchmark label. |
| `pattern_type` | Scenario category. |

Read only by `chainlens.evaluation.evaluate`. It is joined to a prediction file that was
frozen before any label was available, and the evaluator never writes to the case
database.

---

## Validation policies

| | Compatibility | Strict |
| --- | --- | --- |
| Intent | Import the synthetic sample with every problem visible | Enforce the contract |
| Malformed arrays | Quarantine | Quarantine |
| Invalid amounts | Quarantine | Quarantine |
| Residual above tolerance | Accept with warning | Quarantine |
| Non-hex TXID | Accept with warning | Quarantine |
| Unverifiable address checksum | Informational | Informational |
| Invalid IP / port | Warning, field nulled | Warning, field nulled |

Neither mode repairs a fee, invents a missing field or alters an amount. On the supplied
sample: compatibility 9,738 / 262 / 0; strict 9,738 / 247 / 15. Both reconcile to 10,000.

**Why an unverifiable checksum is only informational**, even in strict mode: every
address in the supplied sample fails Base58Check because the data is synthetic.
Quarantining on that basis would reject the whole dataset for a property of how it was
generated. Addresses are treated as opaque identifiers, and the file says so.

---

## Issue codes

Severity `info` is recorded and traceable but does not demote a row from *accepted* — it
describes a property of the dataset rather than a defect in that row.

| Code | Severity | Meaning |
| --- | --- | --- |
| `SCHEMA_MISSING_FIELD` | error | A required field is absent or blank. |
| `ARRAY_LENGTH_MISMATCH` | error | Address and amount arrays differ in length. |
| `ARRAY_EMPTY` / `ARRAY_EMPTY_ELEMENT` | error | Empty array or empty element after splitting. |
| `AMOUNT_INVALID` / `AMOUNT_NEGATIVE` | error | Unreadable or negative amount. |
| `FEE_INVALID` / `FEE_NEGATIVE` | error | Unreadable or negative fee. |
| `FEE_MISSING` | warning | No fee supplied; the residual cannot be attributed. |
| `FEE_RESIDUAL_WITHIN_TOLERANCE` | warning | Residual within the declared tolerance. |
| `FEE_RESIDUAL_ABOVE_TOLERANCE` | warning / error | Above tolerance. Quarantines in strict mode. |
| `TIMESTAMP_INVALID` | error | Unparseable timestamp. |
| `TXID_MISSING` | error | No identifier. |
| `TXID_FORMAT_UNVERIFIED` | warning / error | Not 64 hex characters. |
| `TXID_CONFLICT` | error | Same TXID, different payload. Both quarantined; the first is never overwritten. |
| `DUPLICATE_OBSERVATION` | warning | Same TXID and payload — recorded as an extra observation. |
| `IP_INVALID` / `PORT_INVALID` | warning | Field nulled, row kept. |
| `ADDRESS_CHECKSUM_UNVERIFIED` | info | Kept as an opaque identifier. |
| `ADDRESS_REPEATED_IN_ROW` | warning | Both occurrences preserved. |
| `SCRIPT_TYPE_UNKNOWN` | warning | Preserved as supplied. |
| `LABEL_COLUMN_REMOVED` | info | Recorded once per dataset. |
| `ROW_UNPARSEABLE` | error | The row could not be read at all. |

API errors carry a stable `code`, a `message` and, where applicable, a `row_number`.

---

## Model features

Exactly these 18, in this order. Enforced at runtime — any other column raises.

`tx_count`, `incoming_tx_count`, `outgoing_tx_count`, `distinct_senders`,
`distinct_receivers`, `distinct_counterparties`, `received_sats_log`, `spent_sats_log`,
`mean_incoming_sats_log`, `incoming_value_concentration`, `mean_input_count`,
`mean_output_count`, `equal_output_participation`, `repeated_co_input_partners`,
`median_inter_event_hours`, `activity_span_hours`, `max_tx_in_24h`, `has_multiple_events`

Full definitions: `GET /api/meta/policy` or `chainlens.features.feature_manifest()`.

**Excluded, and why:**

| Excluded | Reason |
| --- | --- |
| Address strings, TXIDs | Identity. Would let the model memorise a dataset instead of learning behaviour. |
| IPs, ports | Network identity. Displayed as context only. |
| `script_type` | Record metadata, and transaction-level rather than address-level. |
| Fee rate | Not computable — the contract has no virtual size. |
| Residual magnitude | A generation artefact, not behaviour. Would be a shortcut to the planted rows. |
| Any label or label proxy | Evaluation only. |

**Missing values.** Timing statistics are undefined for a single-event address. They are
set to `0.0` and paired with `has_multiple_events = 0.0`, so the model can distinguish
"not measured" from "measured as zero". No feature is imputed from another address.

**Counts cover the imported period only.** They are not a wallet balance and not a
complete history for any address.
