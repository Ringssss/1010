"""CPU counterexamples for the audit's material clock and budget risks."""
import unittest
from evidence import allocation_ledger, receiver_envelope


class EvidenceTests(unittest.TestCase):
    def test_cross_host_monotonic_is_rejected(self):
        rows = [dict(worker=w, host=host, boot_id="same-string", start_ns=10, end_ns=20)
                for w, host in [(2, "node29"), (3, "node27")]]
        with self.assertRaisesRegex(ValueError, "cross-host"):
            receiver_envelope(rows)

    def test_cross_boot_monotonic_is_rejected(self):
        rows = [dict(worker=w, host="node29", boot_id=str(w), start_ns=10, end_ns=20)
                for w in [2, 3]]
        with self.assertRaisesRegex(ValueError, "cross-boot"):
            receiver_envelope(rows)

    def test_RPC_overlap_does_not_qualify_worker_contention(self):
        rows = [dict(worker=w, host="node29", boot_id="boot", start_ns=10+w, end_ns=20+w)
                for w in [2, 3]]
        result = receiver_envelope(rows)
        self.assertEqual(result["makespan_ns"], 11)
        self.assertEqual(result["envelope_overlap_ns"], 9)
        self.assertIsNone(result["actual_worker_monotonic"])
        self.assertFalse(result["physical_shared_claim_qualified"])

    def test_copied_regions_are_charged_twice(self):
        rows = [dict(id=str(w), worker=w, tier="hbm", n=4096,
                     identity="same", tp=0, off=0) for w in [0, 1]]
        budget = dict(tier_budgets={"hbm": 8192}, location_budgets={"0:hbm": 4096, "1:hbm": 4096})
        self.assertEqual(allocation_ledger(rows, budget)["logical_copy_bytes"], 8192)
        budget["tier_budgets"]["hbm"] = 4096
        with self.assertRaisesRegex(ValueError, "global"):
            allocation_ledger(rows, budget)

    def test_location_cap_and_page_rounding_are_enforced(self):
        rows = [dict(id="a", worker=0, tier="hbm", n=4097)]
        budget = dict(tier_budgets={"hbm": 8192}, location_budgets={"0:hbm": 4097})
        with self.assertRaisesRegex(ValueError, "location"):
            allocation_ledger(rows, budget)


if __name__ == "__main__":
    unittest.main()
