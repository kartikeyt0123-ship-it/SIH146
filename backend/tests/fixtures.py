"""Purpose-built scenario fixtures.

The generator referenced by the supplied notes (generate_dataset.py) was not provided,
so these fixtures are a documented replacement. They are written by hand for one
behaviour each, with entity names that are disjoint from the supplied sample, so a
detector cannot pass by having memorised the supplied file.

Each builder returns CSV bytes in the documented input format.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import CSV_HEADER

BASE = datetime(2027, 3, 1, 12, 0, 0, tzinfo=timezone.utc)


def ts(hours: float = 0.0) -> str:
    return (BASE + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def btc(sats: int) -> str:
    return f"{sats / 100_000_000:.8f}"


class Builder:
    """Accumulates rows and renders them as a CSV file."""

    def __init__(self) -> None:
        self.rows: list[str] = []
        self._n = 0

    def tx(
        self,
        inputs: list[tuple[str, int]],
        outputs: list[tuple[str, int]],
        hours: float = 0.0,
        fee_sats: int | None = None,
        src_ip: str = "10.1.0.1",
        script_type: str = "P2PKH",
        txid: str | None = None,
    ) -> "Builder":
        self._n += 1
        total_in = sum(a for _, a in inputs)
        total_out = sum(a for _, a in outputs)
        fee = total_in - total_out if fee_sats is None else fee_sats
        self.rows.append(",".join([
            ts(hours), src_ip, "40000", "10.9.9.9", "8333",
            txid or f"{self._n:064x}",
            ";".join(a for a, _ in inputs),
            ";".join(a for a, _ in outputs),
            ";".join(btc(v) for _, v in inputs),
            ";".join(btc(v) for _, v in outputs),
            btc(max(0, fee)), script_type, "0",
        ]))
        return self

    def build(self) -> bytes:
        return ("\r\n".join([CSV_HEADER, *self.rows]) + "\r\n").encode("utf-8")


def coinjoin_like(participants: int = 6, denomination: int = 10_000_000) -> bytes:
    """A collaborative transaction: many inputs, many equal-value outputs."""
    builder = Builder()
    builder.tx(
        inputs=[(f"CJIN{i}", denomination + 5_000 + i * 137) for i in range(participants)],
        outputs=[(f"CJOUT{i}", denomination) for i in range(participants)],
    )
    return builder.build()


def benign_equal_value_batch(recipients: int = 6, amount: int = 5_000_000) -> bytes:
    """A payroll-style batch: ONE input, many equal outputs.

    This is the honest negative for the CoinJoin detector. It shares the equal-value
    output structure but has a single payer, so it must not be tagged collaborative.
    """
    builder = Builder()
    builder.tx(
        inputs=[("PAYROLL", amount * recipients + 50_000)],
        outputs=[(f"STAFF{i}", amount) for i in range(recipients)]
                + [("PAYROLL_CHANGE", 40_000)],
    )
    return builder.build()


def collection_burst(payers: int = 9, amount: int = 30_000_000) -> bytes:
    """Many distinct payers converge on one address, which then moves funds onward."""
    builder = Builder()
    for i in range(payers):
        builder.tx(inputs=[(f"VICTIM{i}", amount + i * 1_000)],
                   outputs=[("COLLECTOR", amount + i * 500)],
                   hours=i * 1.5)
    builder.tx(inputs=[("COLLECTOR", amount * payers // 2)],
               outputs=[("ONWARD", amount * payers // 2 - 20_000), ("SMALL", 10_000)],
               hours=payers * 1.5 + 2)
    return builder.build()


def merchant_like_service(customers: int = 40) -> bytes:
    """A service: value arrives from many parties and is paid back out to many.

    The legitimate lookalike for a collection point. Same convergence, but the money
    leaves again to a comparably large and distinct set of counterparties.
    """
    builder = Builder()
    for i in range(customers):
        builder.tx(inputs=[(f"CUST{i}", 20_000_000 + i * 997)],
                   outputs=[("SERVICE", 19_990_000 + i * 997)], hours=i * 0.4)
    for i in range(customers):
        builder.tx(inputs=[("SERVICE", 19_000_000 + i * 991)],
                   outputs=[(f"WITHDRAW{i}", 18_990_000 + i * 991)],
                   hours=i * 0.4 + 0.2)
    return builder.build()


def peel_chain(hops: int = 5, start: int = 1_000_000_000) -> bytes:
    """A dominant output carried forward while small amounts branch away."""
    builder = Builder()
    value = start
    builder.tx(inputs=[("PEELSRC", value)], outputs=[("HOP0", value - 5_000)], hours=0)
    value -= 5_000
    for i in range(hops):
        peeled = 3_000_000 + i * 11_000
        forward = value - peeled - 2_000
        builder.tx(inputs=[(f"HOP{i}", value)],
                   outputs=[(f"HOP{i + 1}", forward), (f"SIDE{i}", peeled)],
                   hours=(i + 1) * 10)
        value = forward
    return builder.build()


def broken_peel_chain(gap_hours: float = 400.0) -> bytes:
    """A chain whose hops are too far apart in time to be linked.

    Tests that the detector does not connect transactions across an arbitrary gap.
    """
    builder = Builder()
    value = 1_000_000_000
    for i in range(4):
        peeled = 3_000_000
        forward = value - peeled - 2_000
        builder.tx(inputs=[(f"BHOP{i}", value)],
                   outputs=[(f"BHOP{i + 1}", forward), (f"BSIDE{i}", peeled)],
                   hours=i * gap_hours)
        value = forward
    return builder.build()


def repeated_co_spend(times: int = 3) -> bytes:
    """Two addresses repeatedly spent together: the common-input hypothesis."""
    builder = Builder()
    for i in range(times):
        builder.tx(inputs=[("WALLET_A", 50_000_000), ("WALLET_B", 30_000_000)],
                   outputs=[(f"DEST{i}", 79_990_000)], hours=i * 6)
    return builder.build()


def co_spend_only_inside_coinjoin(participants: int = 6) -> bytes:
    """Addresses that co-spend ONLY inside collaborative transactions.

    They must never be clustered: that is precisely where the common-input heuristic
    is known to be wrong.
    """
    builder = Builder()
    for round_index in range(3):
        builder.tx(
            inputs=[(f"MIX{i}", 10_005_000 + i * 311) for i in range(participants)],
            outputs=[(f"MIXOUT{round_index}_{i}", 10_000_000) for i in range(participants)],
            hours=round_index * 5,
        )
    return builder.build()


def shared_ip_unrelated(addresses: int = 5, transactions_each: int = 3) -> bytes:
    """Unrelated addresses that happen to be observed at one endpoint.

    They never co-spend, so nothing may group them. This is the mutation test for the
    shared-IP safeguard.
    """
    builder = Builder()
    hours = 0.0
    for i in range(addresses):
        for j in range(transactions_each):
            builder.tx(inputs=[(f"IND{i}", 10_000_000)],
                       outputs=[(f"IND{i}_DEST{j}", 9_990_000)],
                       hours=hours, src_ip="203.0.113.77")
            hours += 1.0
    return builder.build()


def high_volume_but_ordinary(transactions: int = 120) -> bytes:
    """A busy pass-through address: many senders and many receivers."""
    builder = Builder()
    for i in range(transactions):
        if i % 2 == 0:
            builder.tx(inputs=[(f"IN{i}", 15_000_000)], outputs=[("BUSY", 14_990_000)],
                       hours=i * 0.3)
        else:
            builder.tx(inputs=[("BUSY", 14_000_000)], outputs=[(f"OUT{i}", 13_990_000)],
                       hours=i * 0.3)
    return builder.build()


def fee_discrepancy_rows() -> bytes:
    """Rows whose amounts do not reconcile with the supplied fee."""
    builder = Builder()
    builder.tx(inputs=[("Q1", 100_000_000)], outputs=[("Q2", 99_900_000)], fee_sats=99_999)
    builder.tx(inputs=[("Q3", 100_000_000)], outputs=[("Q4", 99_900_000)], fee_sats=50_000)
    builder.tx(inputs=[("Q5", 100_000_000)], outputs=[("Q6", 99_900_000)], fee_sats=100_000)
    return builder.build()
