#!/usr/bin/env python3
"""CPU rejection checks for real-weight extent ownership and clock boundaries."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.benchmarks.keeper_bridge import targets_for, protected_workers, rpc_window, lease_check


def fixture():
    return {"sources": [dict(tp=i, worker=i, tier="dram", n=128, identity=f"identity{i}",
        meta={"model_identity": "real"}, index={"a": [0, 64], "b": [64, 64]}) for i in (0, 1)]}


class Contracts(unittest.TestCase):
    def test_extent_manifest_identity_and_pair_mapping(self):
        model = fixture()
        rows = targets_for(model, "bridge_test", "pair", 96, 16)
        self.assertEqual([t["worker"] for t in rows], [2, 3])
        self.assertEqual([t["tp"] for t in rows], [0, 1])
        self.assertEqual(rows[0]["identity"], model["sources"][0]["identity"])
        self.assertEqual(rows[0]["index"], model["sources"][0]["index"])
        self.assertEqual((rows[0]["off"], rows[0]["n"], rows[0]["mode"]), (16, 96, "replica"))
        with self.assertRaises(ValueError):
            targets_for(model, "bridge_test", "single_A", 96, 40)
        model["sources"][0]["index"]["b"] = [65, 63]
        with self.assertRaises(ValueError):
            targets_for(model, "bridge_test", "single_A", 96)

    def test_unowned_namespace_and_wrong_source_rejected(self):
        for run in ("parent", "bridge_test/../other"):
            with self.assertRaises(ValueError):
                targets_for(fixture(), run, "pair", 32)
        model = fixture(); model["sources"][1]["worker"] = 2
        with self.assertRaises(ValueError):
            targets_for(model, "bridge_test", "single_B", 32)

    def test_live_target_rejected_and_preservation_never_hides_foreign_id(self):
        workers = [{"replicas": [{"id": "bridge_test/pair/rank0", "state": "VALID", "leases": 0}]}]
        with self.assertRaises(ValueError):
            protected_workers(workers, "bridge_test")
        workers[0]["replicas"][0]["state"] = "RELEASED"
        workers[0]["replicas"].append({"id": "bridge_other/x", "state": "VALID"})
        self.assertEqual(protected_workers(workers, "bridge_test")[0]["replicas"], [workers[0]["replicas"][1]])

    def test_mixed_clock_and_missing_receiver_rejected(self):
        rows = [dict(worker=w, begin_ns=100+w, end_ns=200+w, clock_domain="node29/boot/monotonic") for w in (2, 3)]
        result = rpc_window(rows, [2, 3], "node29/boot/monotonic")
        self.assertEqual(result["end_ns"]-result["begin_ns"], 101)
        self.assertIsNone(result["worker_true_monotonic_window"])
        rows[1]["clock_domain"] = "node27/boot/monotonic"
        with self.assertRaises(ValueError):
            rpc_window(rows, [2, 3], "node29/boot/monotonic")
        with self.assertRaises(ValueError):
            rpc_window(rows[:1], [2, 3], "node29/boot/monotonic")

    def test_source_lease_gate(self):
        rows = [{"replicas": [{"id": "base0", "state": "VALID", "leases": 1}]}]
        self.assertTrue(lease_check(rows, ["base0"], True))
        self.assertFalse(lease_check(rows, ["base0"], False))

    def test_current_retained_model_has_legal_1GiB_extent(self):
        path = ROOT / "codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json"
        model = json.loads(path.read_text())["models"]["B"]
        rows = targets_for(model, "bridge_cpu_test", "pair", 1 << 30)
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
