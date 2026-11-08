"""CPU-only checks for IPC gate clock boundaries and ownership-safe cleanup."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import run_ipc_gate as gate


class GateTests(unittest.TestCase):
    def events(self, directory, rank, host, begin, end):
        rows = [{"event": event, "monotonic_ns": stamp, "host": host}
                for event, stamp in [("load_begin", begin), ("weights_ready", end), ("release_end", end + 1)]]
        (Path(directory) / f"rank{rank}.jsonl").write_text("\n".join(json.dumps(row) for row in rows))

    def test_same_host_window_includes_arrival_skew(self):
        with tempfile.TemporaryDirectory() as directory:
            self.events(directory, 0, "host-a", 10, 20)
            self.events(directory, 1, "host-a", 15, 30)
            timing = gate.load_timing(directory, 2)
            self.assertEqual(timing["primary_T_ready_s"], 20e-9)
            self.assertEqual(len(gate.release_evidence(directory, 2)), 2)

    def test_cross_host_clocks_are_not_subtracted(self):
        with tempfile.TemporaryDirectory() as directory:
            self.events(directory, 0, "host-a", 10, 20)
            self.events(directory, 1, "host-b", 1000000, 1000030)
            timing = gate.load_timing(directory, 2)
            self.assertIsNone(timing["primary_T_ready_s"])
            self.assertEqual(timing["per_host"]["host-b"]["load_s"], 30e-9)

    def test_duplicate_load_or_missing_release_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            self.events(directory, 0, "host-a", 10, 20)
            path = Path(directory) / "rank0.jsonl"
            with path.open("a") as stream:
                stream.write('\n{"event":"load_begin","monotonic_ns":100,"host":"host-a"}')
            with self.assertRaises(ValueError):
                gate.release_evidence(directory, 1)
            with self.assertRaises(ValueError):
                gate.load_timing(directory, 1)

    def test_source_coverage_rejects_gap_and_wrong_identity(self):
        model = {"source_ids": ["a", "b"], "sources": [{"tp": 0, "n": 10, "identity": "x"}]}
        rows = [{"id": "a", "tp": 0, "off": 0, "n": 5, "identity": "x", "tier": "dram"},
                {"id": "b", "tp": 0, "off": 5, "n": 5, "identity": "x", "tier": "dram"}]
        self.assertEqual(len(gate.check_source_coverage(model, rows)), 2)
        rows[1]["off"] = 6
        with self.assertRaises(ValueError):
            gate.check_source_coverage(model, rows)
        rows[1]["off"] = 5
        rows[1]["identity"] = "other"
        with self.assertRaises(ValueError):
            gate.check_source_coverage(model, rows)

    def cleanup_fixture(self):
        obj = gate.IpcGate.__new__(gate.IpcGate)
        obj.own_ids = ["own"]
        obj.parked_verified = False
        obj.result = {"cleanup_errors": []}
        obj.hashes = {}
        obj.save = Mock()
        obj.slot = {"endpoint": "http://fixture"}
        obj.cluster = Mock()
        obj.cluster.inventory.return_value = ([], [{"id": "own"}, {"id": "protected"}])
        return obj

    def test_glm_mixed_base_allowed_qwen_still_dram_only(self):
        model = {"source_ids": ["dram", "ssd"], "sources": [{"tp": 4, "n": 10, "identity": "x"}]}
        rows = [{"id": "ssd", "tp": 4, "off": 0, "n": 2, "identity": "x", "tier": "ssd"},
                {"id": "dram", "tp": 4, "off": 2, "n": 8, "identity": "x", "tier": "dram"}]
        self.assertEqual(len(gate.check_source_coverage(model, rows, ("dram", "ssd"))), 2)
        with self.assertRaises(ValueError):
            gate.check_source_coverage(model, rows)

    def test_failed_consumer_release_retains_owner(self):
        obj = self.cleanup_fixture()
        obj.consumer_may_hold_refs = True
        with patch.object(gate, "request", side_effect=RuntimeError("consumer unavailable")):
            obj.cleanup()
        obj.cluster.retire.assert_not_called()
        self.assertTrue(obj.result["cleanup_errors"])

    def test_cleanup_only_retires_own_replica(self):
        obj = self.cleanup_fixture()
        obj.consumer_may_hold_refs = False
        obj.cleanup()
        obj.cluster.retire.assert_called_once_with("own")


if __name__ == "__main__":
    unittest.main()
