"""CPU contract checks: clocks, byte coverage, NIC attribution and cleanup fail closed."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from inventory import ROOT, new_out, preservation
from run_calibration import collect, payload_evidence
from fluidckpt.benchmarks.topology_calibration import (BUFFER_BYTES, TOTAL_BYTES, counter_deltas,
    overlap, parse_binding, protocol, shared_gate, signature, validate_flow, validate_process_exit)

WORKERS = [{"worker": i, "node": "node27" if i < 2 else "node29", "gpu": i % 2, "numa": 0,
            "epoch": f"epoch{i}", "session": "retained", "transactions": [], "replicas": [],
            "hbm_allocated_bytes": 99, "hbm_reserved_bytes": 100} for i in range(4)]


def flow(fid=0, start=0, end=100, rate=10):
    return {"flow_id": fid, "src": fid, "dst": fid+2, "bytes": TOTAL_BYTES,
            "buffer_bytes": BUFFER_BYTES, "iterations": 16, "role": "target",
            "receiver_start_ns": start, "receiver_end_ns": end,
            "clock_domain": "node29/boot/monotonic_ns", "completion": "cuda_stream_synchronize",
            "receiver_reset_before_timing": True,
            "chunk_intervals": [[start+i*(end-start)//16, start+(i+1)*(end-start)//16] for i in range(16)],
            "correctness": {"passed": True, "sha256": "abc", "expected_sha256": "abc", "verified_bytes": BUFFER_BYTES},
            "empirical_effective_GBps": rate}


def snapshot(value, ns, domain="node29/boot/monotonic_ns", unit=4, port=1):
    return {"values": {f"/sys/class/infiniband/shca_0/ports/{port}/counters/port_rcv_data":
            {"raw": value, "unit_bytes": unit}}, "monotonic_ns": ns, "clock_domain": domain}


class Contracts(unittest.TestCase):
    def test_matrix_has_nine_bounded_cases(self):
        data = protocol(WORKERS, "sessionhash")
        self.assertEqual(len(data["cases"]), 9)
        self.assertEqual({c["node"] for c in data["cases"] if c["kind"] == "h2d"}, {"node27", "node29"})
        self.assertTrue(all(c["bytes_per_flow"] == 4 << 30 for c in data["cases"]))
        self.assertFalse(data["capacity_is_physical_upper_bound"])

    def test_wrong_gpu_topology_rejected(self):
        workers = copy.deepcopy(WORKERS)
        workers[0]["gpu"] = 7
        with self.assertRaises(ValueError):
            protocol(workers, "hash")

    def test_reverse_preserves_case_set(self):
        original = protocol(WORKERS, "hash")["cases"]
        self.assertEqual(protocol(WORKERS, "hash", reverse=True)["cases"], original[::-1])

    def test_empirical_rate_uses_completed_receiver_interval(self):
        case = protocol(WORKERS, "hash", "network")["cases"][0]
        data = validate_flow(flow(), case)
        self.assertAlmostEqual(data["empirical_effective_GBps"], TOTAL_BYTES/100)
        self.assertFalse(data["capacity_is_physical_upper_bound"])

    def test_validation_rejects_wrong_bytes_sha_sync_path_and_warmed_receiver(self):
        case = protocol(WORKERS, "hash", "network")["cases"][0]
        for key, value in (("bytes", 1), ("completion", "enqueue"), ("receiver_reset_before_timing", False),
                           ("dst", 3), ("flow_id", 1)):
            bad = flow(); bad[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_flow(bad, case)
        bad = flow(); bad["correctness"]["verified_bytes"] = 1
        with self.assertRaises(ValueError):
            validate_flow(bad, case)

    def test_iteration_order_and_coverage_rejected(self):
        case = protocol(WORKERS, "hash", "network")["cases"][0]
        for intervals in ([[0, 10]]*16, [[-1, 1]] + flow()["chunk_intervals"][1:],
                          flow()["chunk_intervals"][:-1]):
            bad = flow(); bad["chunk_intervals"] = intervals
            with self.assertRaises(ValueError):
                validate_flow(bad, case)

    def test_cross_clock_overlap_rejected(self):
        other = flow(1); other["clock_domain"] = "node27/boot/monotonic_ns"
        with self.assertRaises(ValueError):
            overlap([flow(), other])

    def test_overlap_fraction_uses_shorter_completed_window(self):
        evidence = overlap([flow(0, 0, 100), flow(1, 20, 120)])
        self.assertEqual(evidence["overlap_fraction"], .8)

    def test_counter_units_and_clock_validation(self):
        self.assertEqual(counter_deltas(snapshot(100, 1), snapshot(110, 2))["deltas"][0]["delta_bytes"], 40)
        for after in (snapshot(99, 2), snapshot(110, 1), snapshot(110, 2, "other"), snapshot(110, 2, unit=3)):
            self.assertFalse(counter_deltas(snapshot(100, 1), after)["deltas"][0]["valid"])

    def test_actual_binding_requires_channel_transport(self):
        selection = "NCCL INFO NET/IB : Using [0]shca_0:1/IB ; OOB ib0:12.11.3.29<0>"
        channel = "NCCL INFO Channel 00 : 0 -> 1 [send] via NET/IBext_v8/0/GDRDMA"
        self.assertTrue(parse_binding(selection + "\n" + channel, "shca_0", "ib0")["passed"])
        self.assertFalse(parse_binding(selection, "shca_0", "ib0")["passed"])
        self.assertFalse(parse_binding(selection + channel, "shca_1", "ib0")["passed"])

    def test_actual_port_payload_and_background(self):
        data = flow()
        data["nic_snapshot_before"] = snapshot(0, 10)
        data["nic_snapshot_after"] = snapshot(TOTAL_BYTES//4, 110)
        data["idle_background_counter_delta"] = counter_deltas(snapshot(0, 1), snapshot(0, 2))
        binding = {"actual_hca_ports": [{"hca": "shca_0", "port": 1}]}
        self.assertTrue(payload_evidence([data], binding)["passed"])
        data["nic_snapshot_after"] = snapshot(10, 110)
        self.assertFalse(payload_evidence([data], binding)["passed"])
        data["nic_snapshot_after"] = snapshot(TOTAL_BYTES//4, 110, port=2)
        self.assertFalse(payload_evidence([data], binding)["passed"])

    def test_pair_counter_is_not_double_counted(self):
        a, b = flow(), flow(1)
        for data in (a, b):
            data["nic_snapshot_before"] = snapshot(0, 10)
            data["nic_snapshot_after"] = snapshot(TOTAL_BYTES//4, 110)
            data["idle_background_counter_delta"] = counter_deltas(snapshot(0, 1), snapshot(0, 2))
        evidence = payload_evidence([a, b], {"actual_hca_ports": [{"hca": "shca_0", "port": 1}]})
        self.assertFalse(evidence["passed"])
        self.assertEqual(evidence["expected_payload_bytes"], 2*TOTAL_BYTES)

    def gate_rows(self, overlap_fraction=.9, pair_rate=8, bg=0):
        return [{"case_id": "network_single_a", "flows": [flow(end=int(TOTAL_BYTES/10), rate=10)]},
                {"case_id": "network_single_b", "flows": [flow(1, end=int(TOTAL_BYTES/10), rate=10)]},
                {"case_id": "network_pair", "flows": [flow(end=int(TOTAL_BYTES/pair_rate), rate=pair_rate),
                    flow(1, end=int(TOTAL_BYTES/pair_rate), rate=pair_rate)],
                 "overlap": {"overlap_fraction": overlap_fraction},
                 "actual_nic_payload_evidence": {"passed": True, "background_fraction_estimate": bg}}]

    def test_gate_qualified_is_only_isolated_shared_path(self):
        result = shared_gate(self.gate_rows(), True, True, True)
        self.assertEqual(result["gate_status"], "qualified")
        self.assertEqual(result["aggregate_pair_to_isolated_rate_ratio"], .8)
        self.assertFalse(result["application_executor_contention_verified"])

    def test_gate_requires_80percent_overlap(self):
        self.assertEqual(shared_gate(self.gate_rows(.79), True, True, True)["gate_status"], "blocked")

    def test_gate_background_or_no_degradation_is_conditional(self):
        self.assertEqual(shared_gate(self.gate_rows(bg=.1), True, True, True)["gate_status"], "conditional")
        self.assertEqual(shared_gate(self.gate_rows(pair_rate=9), True, True, True)["gate_status"], "conditional")

    def test_gate_failed_binding_or_protection_or_cleanup_blocks(self):
        for flags in ((False, True, True), (True, False, True), (True, True, False)):
            self.assertEqual(shared_gate(self.gate_rows(), *flags)["gate_status"], "blocked")

    def test_pair_rate_uses_common_makespan_not_sum_of_flow_rates(self):
        rows = self.gate_rows()
        rows[-1]["flows"][1]["receiver_start_ns"] = int(TOTAL_BYTES/8*.1)
        rows[-1]["flows"][1]["receiver_end_ns"] = int(TOTAL_BYTES/8*1.1)
        result = shared_gate(rows, True, True, True)
        self.assertAlmostEqual(result["aggregate_pair_to_isolated_rate_ratio"], .8/1.1, places=8)

    def test_unequal_independent_flows_do_not_qualify_contention(self):
        rows = self.gate_rows()
        rows[0]["flows"] = [flow(end=int(TOTAL_BYTES/20), rate=20)]
        rows[1]["flows"] = [flow(1, end=int(TOTAL_BYTES/8), rate=8)]
        rows[2]["flows"] = [flow(end=int(TOTAL_BYTES/20), rate=20),
                              flow(1, end=int(TOTAL_BYTES/8), rate=8)]
        result = shared_gate(rows, True, True, True)
        self.assertEqual(result["independence_completion_ratio"], 1)
        self.assertLess(result["aggregate_pair_to_isolated_rate_ratio"], .85)
        self.assertEqual(result["gate_status"], "conditional")

    def test_start_skew_without_slowdown_does_not_qualify_contention(self):
        rows = self.gate_rows(overlap_fraction=.8, pair_rate=10)
        duration = int(TOTAL_BYTES/10)
        rows[2]["flows"][1]["receiver_start_ns"] = int(duration*.2)
        rows[2]["flows"][1]["receiver_end_ns"] = duration+int(duration*.2)
        result = shared_gate(rows, True, True, True)
        self.assertLess(result["independence_completion_ratio"], .85)
        self.assertGreater(result["receiver_start_skew_fraction_of_shorter_single"], .05)
        self.assertEqual(result["gate_status"], "conditional")

    def test_owned_process_exit_requires_matching_launch_record(self):
        registered = {"pid": 100, "start_time": "5", "spec": "owned.json"}
        self.assertTrue(validate_process_exit(registered, registered, {"pid": 100, "live": False}))
        for expected, actual in ((None, {"pid": 100, "live": False}),
                                 (registered, {"pid": 100, "live": True}),
                                 (registered, {"pid": 100, "live": False, "start_time": "99"})):
            with self.assertRaises(ValueError):
                validate_process_exit(expected, registered, actual)

    def test_protected_signature_detects_keeper_memory_and_transaction_changes(self):
        for key, value in (("hbm_reserved_bytes", 200), ("transactions", ["new-tx"]), ("epoch", "changed")):
            changed = copy.deepcopy(WORKERS); changed[0][key] = value
            self.assertNotEqual(signature(WORKERS), signature(changed))

    def test_output_scope_rejects_protected_directory(self):
        with self.assertRaises(ValueError):
            new_out(ROOT / "codex_coding/results/v0.1.23.3/task2/basic_scenarios/qwen_bootstrap_r01")

    def test_collect_duplicate_or_missing_flows_rejected(self):
        prereg = protocol(WORKERS, "hash", "network")
        prereg["cases"] = prereg["cases"][:1]
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            for rank in range(4):
                (out / f"rank_{rank}.json").write_text(json.dumps({"status": "passed", "cases": []}))
                (out / f"rank_{rank}.log").write_text("")
            with self.assertRaises(ValueError):
                collect(out, prereg, "shca_0", "ib0")
            for rank in (2, 3):
                (out / f"rank_{rank}.json").write_text(json.dumps({"status": "passed", "cases": [
                    {"case_id": "network_single_a", "flow": flow()}]}))
            with self.assertRaises(ValueError):
                collect(out, prereg, "shca_0", "ib0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
