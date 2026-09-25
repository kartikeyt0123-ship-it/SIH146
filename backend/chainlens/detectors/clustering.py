"""DET05 - candidate common control from repeated co-spending.

The common-input heuristic says addresses spent together in one transaction may be
controlled by one party. It has two well-known failure modes, and both are handled here:

* collaborative transactions (CoinJoin-like, or simply many-input) break it outright, so
  those transactions are excluded from the evidence entirely;
* a single co-spend is weak, so an edge requires the co-spend to repeat.

A shared IP can never create an edge in this module. Network association is a separate
signal and is kept separate.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from ..db import new_id
from ..graphx.model import CaseData
from .base import (
    CAVEAT_AMOUNT_DISCREPANCY,
    CAVEAT_NOT_INTENT,
    CAVEAT_NO_OWNERSHIP,
    RULE_SEVERITY,
    Evidence,
    Finding,
)
from .config import CommonControlConfig

DETECTOR_KEY = "common_control"

#: Pairs from transactions with more inputs than this are not enumerated, both because
#: the heuristic is least reliable there and to keep the pair count bounded.
_PAIR_ENUMERATION_LIMIT = 12


def _pair_evidence(data: CaseData, config: CommonControlConfig, collaborative: set[int]
                   ) -> tuple[dict[tuple[str, str], list[int]], dict[int, str]]:
    """Co-input pairs -> supporting transaction indices, plus why any tx was excluded."""
    pairs: dict[tuple[str, str], list[int]] = defaultdict(list)
    exclusions: dict[int, str] = {}

    for record in data.transactions:
        addresses = sorted(set(record.input_addresses))
        if len(addresses) < 2:
            continue
        if record.index in collaborative:
            exclusions[record.index] = "tagged CoinJoin-like (collaborative transaction)"
            continue
        if record.input_count >= config.collaborative_input_threshold:
            exclusions[record.index] = (
                f"has {record.input_count} inputs, at or above the collaborative "
                f"threshold of {config.collaborative_input_threshold}")
            continue
        if len(addresses) > _PAIR_ENUMERATION_LIMIT:
            exclusions[record.index] = "too many distinct input addresses to enumerate pairs"
            continue
        for a, b in combinations(addresses, 2):
            pairs[(a, b)].append(record.index)
    return pairs, exclusions


def detect(data: CaseData, config: CommonControlConfig, collaborative: set[int]
           ) -> tuple[list[Finding], list[dict]]:
    """Return cluster findings and the cluster hypothesis records to persist."""
    pairs, exclusions = _pair_evidence(data, config, collaborative)
    supported = {pair: txs for pair, txs in pairs.items()
                 if len(txs) >= config.min_shared_transactions}
    if not supported:
        return [], []

    # Union-find over supported pairs to form candidate clusters.
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        # Always attach to the lexicographically smaller root so that cluster identity
        # does not depend on the order the pairs happened to be visited in.
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for a, b in supported:
        union(a, b)

    members: dict[str, set[str]] = defaultdict(set)
    for address in list(parent):
        members[find(address)].add(address)

    findings: list[Finding] = []
    hypotheses: list[dict] = []

    for root in sorted(members):
        cluster = sorted(members[root])
        if len(cluster) < 2 or len(cluster) > config.max_cluster_size:
            continue
        cluster_set = set(cluster)
        edges = [
            {
                "a": a, "b": b,
                "supporting_txids": [data.tx(i).txid for i in sorted(txs)],
                "shared_transaction_count": len(txs),
                "rule": "repeated_co_input",
            }
            for (a, b), txs in sorted(supported.items()) if a in cluster_set and b in cluster_set
        ]
        tx_indices = sorted({i for (a, b), txs in supported.items()
                             if a in cluster_set and b in cluster_set for i in txs})
        records = [data.tx(i) for i in tx_indices]
        quality_affected = any(r.has_amount_discrepancy for r in records)

        # Exceptions: transactions involving these addresses that the rule deliberately
        # did not count. The analyst sees what was excluded and why.
        exception_notes = sorted({
            f"{data.tx(i).txid}: {reason}"
            for i, reason in exclusions.items()
            if cluster_set & set(data.tx(i).input_addresses)
        })

        strength = min(1.0, sum(e["shared_transaction_count"] for e in edges) / (len(cluster) * 4))
        cluster_id = new_id("clu")
        hypotheses.append({
            "id": cluster_id,
            "members": cluster,
            "edges": edges,
            "rule_version": "repeated_co_input/v1",
            "strength": round(strength, 4),
            "exceptions": exception_notes,
        })

        indicators = {
            "cluster_id": cluster_id,
            "member_count": len(cluster),
            "members": cluster,
            "inferred_edge_count": len(edges),
            "supporting_transaction_count": len(tx_indices),
            "min_shared_transactions_threshold": config.min_shared_transactions,
            "strength": round(strength, 4),
            "excluded_transactions": len(exception_notes),
            "rule_version": "repeated_co_input/v1",
        }
        caveats = [
            CAVEAT_NO_OWNERSHIP, CAVEAT_NOT_INTENT,
            "This hypothesis rests on the common-input heuristic alone. No network "
            "observation contributed to it, and a shared IP would not have been "
            "allowed to.",
        ]
        if exception_notes:
            caveats.append(
                f"{len(exception_notes)} transaction(s) involving these addresses were "
                "excluded from the evidence as possibly collaborative.")
        if quality_affected:
            caveats.append(CAVEAT_AMOUNT_DISCREPANCY)

        for address in cluster:
            findings.append(Finding(
                detector=DETECTOR_KEY,
                subject_type="address",
                subject_id=address,
                pattern_label="Candidate common control",
                role_hypothesis="possible_common_control_member",
                severity=RULE_SEVERITY[DETECTOR_KEY],
                indicators={**indicators, "subject_pairs": [
                    e for e in edges if address in (e["a"], e["b"])]},
                evidence=[Evidence(
                    kind="transaction", txid=r.txid, source_row_id=r.source_row_id,
                    row_number=r.row_number,
                    detail={
                        "observed_at": r.observed_at,
                        "co_spent_addresses": sorted(set(r.input_addresses) & cluster_set),
                        "input_count": r.input_count,
                    },
                ) for r in records if address in r.input_addresses],
                period_start=records[0].observed_at if records else None,
                period_end=records[-1].observed_at if records else None,
                alternatives=[
                    "A shared custodial wallet or exchange spends on behalf of many "
                    "unrelated customers from one set of addresses.",
                    "A payment-batching service co-spends inputs it does not own.",
                ],
                caveats=caveats,
                independent_supports=len(tx_indices),
                amount_quality_affected=quality_affected,
            ))
    return findings, hypotheses


def cluster_edges(hypotheses: list[dict]) -> list[dict]:
    """Inferred common-control edges for the graph overlay."""
    edges: list[dict] = []
    for hypothesis in hypotheses:
        for edge in hypothesis["edges"]:
            edges.append({
                "source": f"addr:{edge['a']}",
                "target": f"addr:{edge['b']}",
                "key": f"cc:{hypothesis['id']}:{edge['a']}:{edge['b']}",
                "kind": "candidate_common_control",
                "rule_version": hypothesis["rule_version"],
                "strength": hypothesis["strength"],
                "supporting_txids": edge["supporting_txids"],
                "note": "Repeated co-spending. Not verified ownership.",
            })
    return edges
