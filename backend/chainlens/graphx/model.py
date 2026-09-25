"""The in-memory analysis view of a case.

Detectors and feature extraction read this structure, never the source archive. It is
built exclusively from the sanitised tables, so no evaluation label can reach it.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..db import read_conn


@dataclass(slots=True)
class Observation:
    """One network sighting of a transaction. Deliberately separate from spending."""

    source_row_id: int
    row_number: int
    observed_at: str
    observed_ts: float
    src_ip: str | None
    src_port: int | None
    dst_ip: str | None
    dst_port: int | None


@dataclass(slots=True)
class TxRecord:
    """A sanitised transaction, with array positions preserved."""

    index: int                      # position in the time-ordered list
    db_id: int
    txid: str
    observed_at: str
    observed_ts: float
    fee_sats: int | None
    total_in_sats: int
    total_out_sats: int
    residual_sats: int
    script_type: str | None
    quality_status: str
    source_row_id: int
    row_number: int
    inputs: list[tuple[str, int]] = field(default_factory=list)   # (address, sats) by position
    outputs: list[tuple[str, int]] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)

    @property
    def input_addresses(self) -> list[str]:
        return [a for a, _ in self.inputs]

    @property
    def output_addresses(self) -> list[str]:
        return [a for a, _ in self.outputs]

    @property
    def input_count(self) -> int:
        return len(self.inputs)

    @property
    def output_count(self) -> int:
        return len(self.outputs)

    @property
    def has_amount_discrepancy(self) -> bool:
        """True when this record's amounts do not reconcile with the supplied fee.

        Structural detectors may still use the record; any amount-derived conclusion
        drawn from it must carry this caveat.
        """
        return self.residual_sats != 0

    @property
    def dominant_output(self) -> tuple[str, int, float] | None:
        """The largest output as (address, sats, share of total output value)."""
        if not self.outputs or self.total_out_sats <= 0:
            return None
        address, amount = max(self.outputs, key=lambda pair: pair[1])
        return address, amount, amount / self.total_out_sats


@dataclass(slots=True)
class AddressActivity:
    """Where one address appears across the case."""

    address: str
    spends: list[int] = field(default_factory=list)    # tx indices where it is an input
    receives: list[int] = field(default_factory=list)  # tx indices where it is an output
    received_sats: int = 0
    spent_sats: int = 0

    @property
    def tx_indices(self) -> list[int]:
        return sorted(set(self.spends) | set(self.receives))


@dataclass(slots=True)
class CaseData:
    """Everything a run needs, indexed for the access patterns detectors use."""

    case_id: str
    dataset_id: str
    transactions: list[TxRecord]
    addresses: dict[str, AddressActivity]
    by_txid: dict[str, int]
    ip_to_tx: dict[str, list[int]]

    @property
    def tx_count(self) -> int:
        return len(self.transactions)

    @property
    def address_count(self) -> int:
        return len(self.addresses)

    @property
    def time_span(self) -> tuple[str, str] | tuple[None, None]:
        if not self.transactions:
            return None, None
        return self.transactions[0].observed_at, self.transactions[-1].observed_at

    def tx(self, index: int) -> TxRecord:
        return self.transactions[index]

    def coverage(self) -> dict[str, object]:
        discrepant = sum(1 for t in self.transactions if t.has_amount_discrepancy)
        start, end = self.time_span
        return {
            "transactions": self.tx_count,
            "addresses": self.address_count,
            "observations": sum(len(t.observations) for t in self.transactions),
            "transactions_with_amount_discrepancy": discrepant,
            "distinct_src_ips": len(self.ip_to_tx),
            "period_start": start,
            "period_end": end,
        }


def load_case_data(case_id: str, dataset_id: str | None = None) -> CaseData:
    """Load the sanitised records for a case into the analysis view."""
    tx_sql = (
        "SELECT t.id, t.txid, t.observed_at, t.observed_ts, t.fee_sats, t.total_in_sats,"
        " t.total_out_sats, t.residual_sats, t.script_type, t.quality_status,"
        " t.source_row_id, s.row_number"
        " FROM transactions t JOIN source_rows s ON s.id = t.source_row_id"
        " WHERE t.case_id = ?"
    )
    params: list[object] = [case_id]
    if dataset_id:
        tx_sql += " AND t.dataset_id = ?"
        params.append(dataset_id)
    # Ties are broken by txid so that ordering - and therefore every derived result - is
    # deterministic for a given input.
    tx_sql += " ORDER BY t.observed_ts ASC, t.txid ASC"

    transactions: list[TxRecord] = []
    by_txid: dict[str, int] = {}
    by_db_id: dict[int, int] = {}

    with read_conn() as conn:
        for index, row in enumerate(conn.execute(tx_sql, params)):
            record = TxRecord(
                index=index, db_id=row["id"], txid=row["txid"],
                observed_at=row["observed_at"], observed_ts=row["observed_ts"],
                fee_sats=row["fee_sats"], total_in_sats=row["total_in_sats"],
                total_out_sats=row["total_out_sats"], residual_sats=row["residual_sats"],
                script_type=row["script_type"], quality_status=row["quality_status"],
                source_row_id=row["source_row_id"], row_number=row["row_number"],
            )
            transactions.append(record)
            by_txid[record.txid] = index
            by_db_id[record.db_id] = index

        addresses: dict[str, AddressActivity] = {}
        io_sql = ("SELECT transaction_id, side, position, address, amount_sats FROM tx_io"
                  " WHERE case_id = ? ORDER BY transaction_id, side, position")
        for row in conn.execute(io_sql, (case_id,)):
            index = by_db_id.get(row["transaction_id"])
            if index is None:
                continue
            record = transactions[index]
            address, amount = row["address"], row["amount_sats"]
            activity = addresses.get(address)
            if activity is None:
                activity = addresses[address] = AddressActivity(address)
            if row["side"] == "input":
                record.inputs.append((address, amount))
                activity.spends.append(index)
                activity.spent_sats += amount
            else:
                record.outputs.append((address, amount))
                activity.receives.append(index)
                activity.received_sats += amount

        ip_to_tx: dict[str, list[int]] = defaultdict(list)
        obs_sql = ("SELECT o.transaction_id, o.source_row_id, o.observed_at, o.observed_ts,"
                   " o.src_ip, o.src_port, o.dst_ip, o.dst_port, s.row_number"
                   " FROM observations o JOIN source_rows s ON s.id = o.source_row_id"
                   " WHERE o.case_id = ? ORDER BY o.observed_ts, o.id")
        for row in conn.execute(obs_sql, (case_id,)):
            index = by_db_id.get(row["transaction_id"])
            if index is None:
                continue
            transactions[index].observations.append(Observation(
                source_row_id=row["source_row_id"], row_number=row["row_number"],
                observed_at=row["observed_at"], observed_ts=row["observed_ts"],
                src_ip=row["src_ip"], src_port=row["src_port"],
                dst_ip=row["dst_ip"], dst_port=row["dst_port"],
            ))
            if row["src_ip"]:
                ip_to_tx[row["src_ip"]].append(index)

    return CaseData(
        case_id=case_id,
        dataset_id=dataset_id or "",
        transactions=transactions,
        addresses=addresses,
        by_txid=by_txid,
        ip_to_tx={ip: sorted(set(v)) for ip, v in ip_to_tx.items()},
    )
