"""Profile the supplied sample files. Recomputes every statistic from the files.

Run:  backend/.venv/Scripts/python.exe scripts/profile_dataset.py
This script is a development aid only; it is not part of the analysis pipeline.
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TX = ROOT / "data" / "samples" / "ps3_transactions"
GT = ROOT / "data" / "samples" / "ps4_ground_truth"

SAT = Decimal(10) ** 8


def to_sats(text: str) -> int:
    return int((Decimal(text.strip()) * SAT).to_integral_value())


def main() -> int:
    with TX.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    print(f"transactions file: {TX.name}")
    print(f"  rows                : {len(rows)}")
    print(f"  columns             : {list(rows[0].keys())}")

    txids = Counter(r["txid"] for r in rows)
    print(f"  distinct txids      : {len(txids)}  (duplicated: {sum(1 for v in txids.values() if v > 1)})")
    print(f"  txid length set     : {sorted({len(t) for t in txids})}")
    print(f"  txid all hex        : {all(all(c in '0123456789abcdefABCDEF' for c in t) for t in txids)}")

    ts = sorted(r["timestamp"] for r in rows)
    print(f"  time coverage       : {ts[0]} .. {ts[-1]}")

    print(f"  script_type         : {dict(Counter(r['script_type'] for r in rows))}")
    print(f"  is_planted_susp     : {dict(Counter(r['is_planted_suspicious'] for r in rows))}")
    print(f"  dst_port            : {dict(Counter(r['dst_port'] for r in rows).most_common(5))}")
    print(f"  src_port range      : {min(int(r['src_port']) for r in rows)}..{max(int(r['src_port']) for r in rows)}")

    # array field structure
    len_mismatch = 0
    empty_elem = 0
    in_counts, out_counts = Counter(), Counter()
    residual_rows = 0
    residual_le_1sat = 0
    residual_gt_1sat = 0
    max_abs_residual = 0
    addresses = set()
    addr_len = Counter()
    addr_prefix = Counter()
    dup_within_row = 0

    for r in rows:
        ia = r["input_addresses"].split(";")
        oa = r["output_addresses"].split(";")
        iam = r["input_amounts"].split(";")
        oam = r["output_amounts"].split(";")
        if len(ia) != len(iam) or len(oa) != len(oam):
            len_mismatch += 1
        if any(not x.strip() for x in ia + oa + iam + oam):
            empty_elem += 1
        in_counts[len(ia)] += 1
        out_counts[len(oa)] += 1
        if len(set(ia)) != len(ia) or len(set(oa)) != len(oa):
            dup_within_row += 1
        for a in ia + oa:
            addresses.add(a)
            addr_len[len(a)] += 1
            addr_prefix[a[:1]] += 1
        tin = sum(to_sats(x) for x in iam)
        tout = sum(to_sats(x) for x in oam)
        fee = to_sats(r["fee"])
        resid = tin - tout - fee
        if resid != 0:
            residual_rows += 1
            if abs(resid) <= 1:
                residual_le_1sat += 1
            else:
                residual_gt_1sat += 1
            max_abs_residual = max(max_abs_residual, abs(resid))

    print(f"  array len mismatch  : {len_mismatch}")
    print(f"  rows w/ empty elem  : {empty_elem}")
    print(f"  rows w/ dup addr    : {dup_within_row}")
    print(f"  input count dist    : {dict(sorted(in_counts.items()))}")
    print(f"  output count dist   : {dict(sorted(out_counts.items()))}")
    print(f"  distinct addresses  : {len(addresses)}")
    print(f"  address lengths     : {dict(sorted(addr_len.items()))}")
    print(f"  address prefixes    : {dict(addr_prefix.most_common(6))}")
    print(f"  fee residual rows   : {residual_rows} (<=1 sat: {residual_le_1sat}, >1 sat: {residual_gt_1sat})")
    print(f"  max abs residual    : {max_abs_residual} sats = {Decimal(max_abs_residual) / SAT} BTC")

    with GT.open(newline="", encoding="utf-8") as fh:
        gt = list(csv.DictReader(fh))
    print(f"\nground truth file: {GT.name}")
    print(f"  rows                : {len(gt)}")
    print(f"  distinct wallet_id  : {len({g['wallet_id'] for g in gt})}")
    print(f"  is_suspicious       : {dict(Counter(g['is_suspicious'] for g in gt))}")
    pt = Counter((g["pattern_type"], g["is_suspicious"]) for g in gt)
    for (p, s), n in sorted(pt.items(), key=lambda kv: -kv[1]):
        print(f"    {p:<32} flag={s}  count={n}")

    gt_ids = {g["wallet_id"] for g in gt}
    print(f"\n  tx addresses not in GT : {len(addresses - gt_ids)}")
    print(f"  GT ids not in tx       : {len(gt_ids - addresses)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
