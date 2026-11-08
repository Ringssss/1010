"""Reproduce active-tx inventory rejection and verify lease cleanup precedes snapshot."""
import unittest
from unittest.mock import patch
import recover_target


class Transaction:
    def __init__(self):
        self.active = True
        self.leases = 1
        self.target_weight_bytes = 35164859056
        self.events = []
    def finish(self, tx):
        if tx != "owned_tx" or not self.active:
            raise ValueError("unexpected or repeated transaction finish")
        self.active = False
        self.leases = 0
        self.events.append("finish")
    def inventory(self, state):
        if self.active:
            raise ValueError("mixed keeper sessions or active transactions")
        if self.leases:
            raise ValueError("source lease leaked")
        self.events.append("ready_inventory")
        return [{"free_bytes": 28 << 30, "live_target_weight_bytes": self.target_weight_bytes}], []


class RecoveryOrder(unittest.TestCase):
    def test_previous_order_is_rejected(self):
        transaction = Transaction()
        with self.assertRaisesRegex(ValueError, "active transactions"):
            transaction.inventory({})
        self.assertTrue(transaction.active)

    def test_finish_precedes_ready_and_target_remains_live(self):
        transaction = Transaction()
        flow = {"tx": "owned_tx"}
        with patch.object(recover_target, "retained_inventory", transaction.inventory):
            workers, replicas = recover_target.finish_then_snapshot(transaction, flow, {})
        self.assertEqual(transaction.events, ["finish", "ready_inventory"])
        self.assertEqual(transaction.leases, 0)
        self.assertEqual(workers[0]["live_target_weight_bytes"], 35164859056)
        self.assertTrue(flow["transaction_finished"])
        self.assertEqual(replicas, [])


if __name__ == "__main__":
    unittest.main()
