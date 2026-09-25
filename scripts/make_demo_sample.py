"""Build a small, hand-written sample that exercises every detector.

The generator referenced by the supplied notes was not provided, so this is a documented
replacement. Entity names are deliberately readable (VICTIM3, EXCHANGE, CJIN0) and
disjoint from the supplied dataset, so you can see at a glance whether a detection landed
on the right address.

Run:
    backend/.venv/Scripts/python.exe scripts/make_demo_sample.py

Writes data/samples/demo_mixed_patterns.csv plus a .key.csv describing what was planted.
The key is for your eyes only - it is never imported, and the application would strip its
columns at the boundary if it were.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "samples" / "demo_mixed_patterns.csv"
KEY = ROOT / "data" / "samples" / "demo_mixed_patterns.key.csv"

BASE = datetime(2027, 3, 1, 9, 0, 0, tzinfo=timezone.utc)
HEADER = [
    "timestamp", "src_ip", "src_port", "dst_ip", "dst_port", "txid",
    "input_addresses", "output_addresses", "input_amounts", "output_amounts",
    "fee", "script_type", "is_planted_suspicious",
]

rows: list[list[str]] = []
key: dict[str, str] = {}
counter = 0


def btc(sats: int) -> str:
    return f"{sats / 100_000_000:.8f}"


def tx(inputs, outputs, hours, src_ip="10.20.0.1", fee_sats=None,
       script="P2PKH", planted="0") -> None:
    global counter
    counter += 1
    total_in = sum(v for _, v in inputs)
    total_out = sum(v for _, v in outputs)
    fee = total_in - total_out if fee_sats is None else fee_sats
    rows.append([
        (BASE + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        src_ip, str(40000 + counter % 20000), "10.99.99.9", "8333",
        f"{counter:064x}",
        ";".join(a for a, _ in inputs),
        ";".join(a for a, _ in outputs),
        ";".join(btc(v) for _, v in inputs),
        ";".join(btc(v) for _, v in outputs),
        btc(max(0, fee)), script, planted,
    ])


def label(address: str, what: str) -> None:
    key[address] = what


# --- 1. Ordinary background activity -------------------------------------------------
# Plain one-in-one-out payments between unrelated parties. Nothing should fire here.
for i in range(60):
    tx([(f"NORMAL_IN{i}", 25_000_000 + i * 7_919)],
       [(f"NORMAL_OUT{i}", 24_990_000 + i * 7_919)],
       hours=i * 0.7, src_ip=f"192.0.2.{i % 200 + 1}")
    label(f"NORMAL_IN{i}", "normal")
    label(f"NORMAL_OUT{i}", "normal")

# --- 2. CoinJoin-like structure (DET01) ----------------------------------------------
# 8 inputs, 8 identical outputs. Should be tagged structurally, and excluded from
# common-input clustering.
for round_index in range(2):
    tx([(f"CJIN{round_index}_{i}", 10_000_000 + 4_000 + i * 311) for i in range(8)],
       [(f"CJOUT{round_index}_{i}", 10_000_000) for i in range(8)],
       hours=12 + round_index * 6, script="P2WSH", planted="1")
    for i in range(8):
        label(f"CJIN{round_index}_{i}", "coinjoin participant (input)")
        label(f"CJOUT{round_index}_{i}", "coinjoin participant (output)")

# --- 3. Benign equal-value batch (DET01 negative) ------------------------------------
# Same equal-output shape, but ONE payer. Must NOT be called collaborative.
tx([("PAYROLL", 6 * 5_000_000 + 60_000)],
   [(f"STAFF{i}", 5_000_000) for i in range(6)] + [("PAYROLL_CHANGE", 50_000)],
   hours=20)
label("PAYROLL", "benign payroll batch - must NOT be flagged as CoinJoin")
for i in range(6):
    label(f"STAFF{i}", "payroll recipient - normal")

# --- 4. Collection + participants (DET02/DET03) --------------------------------------
# 12 distinct payers converge on one address in a burst, which then peels onward.
for i in range(12):
    tx([(f"VICTIM{i}", 30_000_000 + i * 90_000)],
       [("COLLECTOR", 29_990_000 + i * 90_000)],
       hours=30 + i * 1.5, planted="1")
    label(f"VICTIM{i}", "payment participant (payer into COLLECTOR)")
label("COLLECTOR", "COLLECTION POINT - expect the top alert")

# --- 5. Peel chain out of the collector (DET04) --------------------------------------
value = 12 * 30_000_000
tx([("COLLECTOR", value)], [("PEEL0", value - 2_500_000 - 3_000), ("PEELSIDE0", 2_500_000)],
   hours=52, planted="1")
label("PEEL0", "peel chain continuation")
label("PEELSIDE0", "peel chain side recipient")
value = value - 2_500_000 - 3_000
for i in range(4):
    peeled = 2_000_000 + i * 150_000
    forward = value - peeled - 3_000
    tx([(f"PEEL{i}", value)],
       [(f"PEEL{i + 1}", forward), (f"PEELSIDE{i + 1}", peeled)],
       hours=60 + i * 11, planted="1")
    label(f"PEEL{i + 1}", "peel chain continuation")
    label(f"PEELSIDE{i + 1}", "peel chain side recipient")
    value = forward

# --- 6. Repeated co-spend (DET05) ----------------------------------------------------
# Two addresses spent together four times: the common-input hypothesis.
for i in range(4):
    tx([("WALLET_A", 40_000_000), ("WALLET_B", 25_000_000)],
       [(f"CCDEST{i}", 64_990_000)], hours=70 + i * 5)
    label(f"CCDEST{i}", "normal")
label("WALLET_A", "CANDIDATE COMMON CONTROL (co-spent with WALLET_B)")
label("WALLET_B", "CANDIDATE COMMON CONTROL (co-spent with WALLET_A)")

# --- 7. Shared IP, unrelated parties (DET06) -----------------------------------------
# Five addresses behind one endpoint that never co-spend. Must be shown, never merged.
for i in range(5):
    for j in range(3):
        tx([(f"NAT{i}", 12_000_000)], [(f"NAT{i}_DEST{j}", 11_990_000)],
           hours=90 + i * 3 + j, src_ip="203.0.113.77")
        label(f"NAT{i}_DEST{j}", "normal")
    label(f"NAT{i}", "shared-IP innocent - must NOT be grouped with the others")

# --- 8. Legitimate high-volume service (DET07) ---------------------------------------
# Value in from many, out to many: the collection lookalike that must be demoted.
for i in range(45):
    tx([(f"DEPOSIT{i}", 22_000_000 + i * 811)], [("EXCHANGE", 21_990_000 + i * 811)],
       hours=110 + i * 0.35, src_ip="198.51.100.20")
    tx([("EXCHANGE", 20_000_000 + i * 733)], [(f"WITHDRAW{i}", 19_990_000 + i * 733)],
       hours=110 + i * 0.35 + 0.15, src_ip="198.51.100.20")
    label(f"DEPOSIT{i}", "normal")
    label(f"WITHDRAW{i}", "normal")
label("EXCHANGE", "LEGITIMATE SERVICE - must NOT become a high-priority lead")

# --- 9. Data-quality rows -------------------------------------------------------------
# Amounts that do not reconcile with the stated fee: one within tolerance, one above.
tx([("QUALITY_A", 50_000_000)], [("QUALITY_B", 49_900_000)], hours=160, fee_sats=99_999)
tx([("QUALITY_C", 50_000_000)], [("QUALITY_D", 49_900_000)], hours=161, fee_sats=40_000)
for name in ("QUALITY_A", "QUALITY_B", "QUALITY_C", "QUALITY_D"):
    label(name, "normal (row carries a fee-conservation residual)")

rows.sort(key=lambda r: r[0])

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", newline="", encoding="utf-8") as fh:
    writer = csv.writer(fh, lineterminator="\r\n")
    writer.writerow(HEADER)
    writer.writerows(rows)

with KEY.open("w", newline="", encoding="utf-8") as fh:
    writer = csv.writer(fh, lineterminator="\r\n")
    writer.writerow(["address", "what_was_planted"])
    for address in sorted(key):
        writer.writerow([address, key[address]])

print(f"wrote {OUT.relative_to(ROOT)}  ({len(rows)} transactions)")
print(f"wrote {KEY.relative_to(ROOT)}  ({len(key)} labelled addresses)")
print("\nExpected on import: all rows accepted, 2 fee-residual warnings, "
      "label column stripped.")
