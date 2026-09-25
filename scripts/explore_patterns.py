"""EXPLORATORY ONLY (development aid, not shipped in the analysis pipeline).

Joins ground truth to the transaction sample purely to understand the *shape* of
each planted pattern so that detector thresholds and independent test fixtures can
be designed. No output of this script is hardcoded into detectors.
"""
from __future__ import annotations

import csv
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAT = Decimal(10) ** 8

rows = list(csv.DictReader((ROOT / "data/samples/ps3_transactions").open(newline="", encoding="utf-8")))
gt = {g["wallet_id"]: g["pattern_type"] for g in csv.DictReader((ROOT / "data/samples/ps4_ground_truth").open(newline="", encoding="utf-8"))}

def sats(x): return int((Decimal(x.strip()) * SAT).to_integral_value())

tx_by_pattern = defaultdict(set)
for i, r in enumerate(rows):
    pats = {gt.get(a, "?") for a in r["input_addresses"].split(";") + r["output_addresses"].split(";")}
    for p in pats:
        tx_by_pattern[p].add(i)

print("transactions touching each pattern:")
for p, s in sorted(tx_by_pattern.items(), key=lambda kv: -len(kv[1])):
    print(f"  {p:<30} {len(s)}")

def show(pat, n=3):
    print(f"\n--- {pat} sample rows ---")
    for i in sorted(tx_by_pattern[pat])[:n]:
        r = rows[i]
        ia, oa = r["input_addresses"].split(";"), r["output_addresses"].split(";")
        iam = [sats(x) for x in r["input_amounts"].split(";")]
        oam = [sats(x) for x in r["output_amounts"].split(";")]
        print(f"  row{i} {r['timestamp']} in={len(ia)} out={len(oa)} planted={r['is_planted_suspicious']}")
        print(f"     in_sats ={iam}")
        print(f"     out_sats={oam}")
        print(f"     out_roles={[gt.get(a,'?') for a in oa]}")
        print(f"     in_roles ={[gt.get(a,'?') for a in ia]}")

for p in ("coinjoin_mixing", "ransomware_collector", "peel_chain_hop", "same_actor_cluster", "legit_exchange_high_volume"):
    show(p)

# shared IP structure
print("\n--- shared_ip_innocent_noise ---")
noise = {a for a, p in gt.items() if p == "shared_ip_innocent_noise"}
ips = Counter()
for r in rows:
    if noise & set(r["input_addresses"].split(";") + r["output_addresses"].split(";")):
        ips[r["src_ip"]] += 1
print("  src_ip counts:", ips.most_common(8))
allip = Counter(r["src_ip"] for r in rows)
print("  top src_ip overall:", allip.most_common(5))
print("  distinct src_ip:", len(allip), " distinct dst_ip:", len(Counter(r['dst_ip'] for r in rows)))

# equal-output structure for coinjoin
print("\n--- equal-output analysis over all rows ---")
eq = Counter()
for r in rows:
    oam = [sats(x) for x in r["output_amounts"].split(";")]
    if len(oam) >= 3:
        c = Counter(oam).most_common(1)[0][1]
        eq[c] += 1
print("  max repeated identical output value, distribution:", dict(sorted(eq.items())))
