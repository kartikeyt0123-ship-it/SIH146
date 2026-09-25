"""Typed graph construction and bounded neighbourhood extraction.

The transaction node is always preserved between inputs and outputs. The product never
asserts a direct payment from a specific input address to a specific output address,
because the supplied contract has no previous-output references to support that.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Iterable

import networkx as nx

from .model import CaseData


class NodeKind(str, Enum):
    ADDRESS = "address"
    TRANSACTION = "transaction"
    ENDPOINT = "endpoint"      # an observed IP endpoint, not an owner


class EdgeKind(str, Enum):
    """Edge provenance. The interface draws each kind differently and labels it."""

    #: Supplied by the source records: this address is an input of this transaction.
    SPEND = "spend"
    #: Supplied by the source records: this transaction pays this address.
    RECEIVE = "receive"
    #: A network sighting. Never implies ownership or transaction origin.
    OBSERVATION = "observation"
    #: Inferred: a dominant output appears to continue into a later transaction.
    INFERRED_CONTINUITY = "inferred_continuity"
    #: Inferred: repeated co-spending suggests possible common control.
    CANDIDATE_COMMON_CONTROL = "candidate_common_control"


#: Edge kinds that are read directly from the supplied data rather than inferred.
OBSERVED_EDGE_KINDS = frozenset({EdgeKind.SPEND, EdgeKind.RECEIVE, EdgeKind.OBSERVATION})


def address_node(address: str) -> str:
    return f"addr:{address}"


def tx_node(txid: str) -> str:
    return f"tx:{txid}"


def endpoint_node(ip: str) -> str:
    return f"ip:{ip}"


def build_graph(data: CaseData, include_observations: bool = True) -> nx.MultiDiGraph:
    """Build the bipartite address/transaction graph plus separate observation edges."""
    graph = nx.MultiDiGraph()

    for address, activity in data.addresses.items():
        graph.add_node(address_node(address), kind=NodeKind.ADDRESS.value, label=address,
                       address=address, received_sats=activity.received_sats,
                       spent_sats=activity.spent_sats,
                       tx_count=len(activity.tx_indices))

    for record in data.transactions:
        graph.add_node(
            tx_node(record.txid), kind=NodeKind.TRANSACTION.value, label=record.txid[:12],
            txid=record.txid, observed_at=record.observed_at, observed_ts=record.observed_ts,
            input_count=record.input_count, output_count=record.output_count,
            total_in_sats=record.total_in_sats, total_out_sats=record.total_out_sats,
            fee_sats=record.fee_sats, residual_sats=record.residual_sats,
            amount_discrepancy=record.has_amount_discrepancy,
            script_type=record.script_type, row_number=record.row_number,
        )
        for position, (address, amount) in enumerate(record.inputs):
            graph.add_edge(address_node(address), tx_node(record.txid),
                           key=f"in:{position}", kind=EdgeKind.SPEND.value, observed=True,
                           position=position, amount_sats=amount, txid=record.txid)
        for position, (address, amount) in enumerate(record.outputs):
            graph.add_edge(tx_node(record.txid), address_node(address),
                           key=f"out:{position}", kind=EdgeKind.RECEIVE.value, observed=True,
                           position=position, amount_sats=amount, txid=record.txid)

        if include_observations:
            for obs in record.observations:
                if not obs.src_ip:
                    continue
                node = endpoint_node(obs.src_ip)
                if node not in graph:
                    graph.add_node(node, kind=NodeKind.ENDPOINT.value, label=obs.src_ip,
                                   ip=obs.src_ip)
                graph.add_edge(node, tx_node(record.txid), key=f"obs:{obs.source_row_id}",
                               kind=EdgeKind.OBSERVATION.value, observed=True,
                               src_port=obs.src_port, dst_ip=obs.dst_ip,
                               dst_port=obs.dst_port, observed_at=obs.observed_at,
                               row_number=obs.row_number, txid=record.txid)
    return graph


def add_inferred_edges(graph: nx.MultiDiGraph, edges: Iterable[dict[str, Any]]) -> None:
    """Overlay inferred edges, each tagged with the rule that produced it."""
    for edge in edges:
        graph.add_edge(
            edge["source"], edge["target"], key=edge.get("key"),
            kind=edge["kind"], observed=False,
            rule_version=edge.get("rule_version", ""),
            strength=edge.get("strength"),
            supporting_txids=edge.get("supporting_txids", []),
            note=edge.get("note", ""),
        )


def neighbourhood(
    graph: nx.MultiDiGraph,
    seeds: list[str],
    hops: int = 1,
    node_budget: int = 300,
) -> tuple[nx.MultiDiGraph, dict[str, Any]]:
    """Return a bounded subgraph around ``seeds``.

    The whole graph is never rendered by default. Expansion stops at ``node_budget`` and
    the returned metadata states plainly that the view was truncated, so the analyst is
    never shown a silently partial picture.
    """
    present = [s for s in seeds if s in graph]
    selected: list[str] = []
    seen: set[str] = set()
    frontier = list(dict.fromkeys(present))
    truncated = False
    reached_hop = 0

    for hop in range(hops + 1):
        next_frontier: list[str] = []
        for node in frontier:
            if node in seen:
                continue
            if len(selected) >= node_budget:
                truncated = True
                break
            seen.add(node)
            selected.append(node)
            reached_hop = hop
            if hop < hops:
                neighbours = set(graph.successors(node)) | set(graph.predecessors(node))
                next_frontier.extend(sorted(n for n in neighbours if n not in seen))
        if len(selected) >= node_budget and next_frontier:
            truncated = True
        if truncated or not next_frontier:
            break
        frontier = next_frontier

    sub = graph.subgraph(selected).copy()
    total_neighbours = set()
    for node in present:
        total_neighbours |= set(graph.successors(node)) | set(graph.predecessors(node))

    meta = {
        "seeds": present,
        "missing_seeds": [s for s in seeds if s not in graph],
        "hops_requested": hops,
        "hops_reached": reached_hop,
        "node_budget": node_budget,
        "nodes_returned": sub.number_of_nodes(),
        "edges_returned": sub.number_of_edges(),
        "truncated": truncated,
        "direct_neighbour_count": len(total_neighbours),
    }
    return sub, meta


def graph_to_payload(sub: nx.MultiDiGraph, meta: dict[str, Any]) -> dict[str, Any]:
    """Serialise a subgraph for the interface, keeping edge provenance explicit."""
    nodes = [{"id": node_id, **{k: v for k, v in attrs.items()}} for node_id, attrs in sub.nodes(data=True)]
    edges = []
    for source, target, key, attrs in sub.edges(data=True, keys=True):
        edges.append({
            "id": f"{source}|{key}|{target}",
            "source": source, "target": target,
            "inferred": not attrs.get("observed", True),
            **{k: v for k, v in attrs.items()},
        })
    return {
        "nodes": sorted(nodes, key=lambda n: n["id"]),
        "edges": sorted(edges, key=lambda e: e["id"]),
        "meta": meta,
        "legend": {
            EdgeKind.SPEND.value: "Supplied: address is an input of the transaction",
            EdgeKind.RECEIVE.value: "Supplied: transaction pays the address",
            EdgeKind.OBSERVATION.value: "Network sighting only - not ownership or origin",
            EdgeKind.INFERRED_CONTINUITY.value: "Inferred: dominant output appears to continue",
            EdgeKind.CANDIDATE_COMMON_CONTROL.value: "Inferred: repeated co-spending",
        },
    }
