"""Reject evidence corruption at the audit boundary without GPUs or RPCs."""
import copy
import unittest

from report import Audit, pair_statistics


def flow():
    return {"flow_id": 0, "receiver_start_ns": 10, "receiver_end_ns": 110,
            "chunk_intervals": [[10, 50], [60, 110]], "iterations": 2,
            "bytes": 200, "buffer_bytes": 100, "completion": "cuda_stream_synchronize",
            "empirical_effective_GBps": 2.0,
            "correctness": {"passed": True, "sha256": "same", "expected_sha256": "same", "verified_bytes": 100}}


class EvidenceCorruptionTests(unittest.TestCase):
    def validate(self, value):
        audit = Audit()
        audit.flow(value)
        return {c["check"]: c["passed"] for c in audit.checks}

    def test_completed_correct_transfer(self):
        self.assertTrue(all(self.validate(flow()).values()))

    def test_unequal_independent_flows_not_contention(self):
        a, b = flow(), flow()
        a["receiver_end_ns"], b["receiver_end_ns"] = 110, 410
        b["flow_id"] = 1
        stats = pair_statistics([a, b], [a, b])
        self.assertEqual(stats["independent_makespan_ratio"], 1)
        self.assertFalse(stats["pressure_exploration_flag"])
        self.assertEqual(stats["per_flow_slowdown"], {"0": 1, "1": 1})

    def test_common_slowdown_is_reported(self):
        a, b = flow(), flow()
        b["flow_id"] = 1
        slowed = copy.deepcopy([a, b])
        for f in slowed:
            f["receiver_end_ns"] = 210
        stats = pair_statistics(slowed, [a, b])
        self.assertEqual(stats["independent_makespan_ratio"], .5)
        self.assertTrue(stats["pressure_exploration_flag"])

    def test_start_skew_prevents_shared_qualification(self):
        a, b = flow(), flow()
        b["flow_id"] = 1
        skewed = copy.deepcopy([a, b])
        skewed[1]["receiver_start_ns"] += 20
        skewed[1]["receiver_end_ns"] += 20
        stats = pair_statistics(skewed, [a, b])
        self.assertLess(stats["independent_makespan_ratio"], .85)
        self.assertFalse(stats["start_control_qualified"])
        self.assertFalse(stats["pressure_exploration_flag"])
        self.assertEqual(stats["per_flow_slowdown"], {"0": 1, "1": 1})

    def test_interval_outside_completed_window(self):
        value = flow()
        value["chunk_intervals"][-1][-1] = 120
        self.assertFalse(self.validate(value)["flow:0:completed_ordered_intervals"])

    def test_overlapping_serial_chunks(self):
        value = flow()
        value["chunk_intervals"][1][0] = 40
        self.assertFalse(self.validate(value)["flow:0:completed_ordered_intervals"])

    def test_fabricated_rate(self):
        value = flow()
        value["empirical_effective_GBps"] = 20
        self.assertFalse(self.validate(value)["flow:0:reported_rate_recomputed"])

    def test_truncated_hash_coverage(self):
        value = flow()
        value["correctness"]["verified_bytes"] = 1
        self.assertFalse(self.validate(value)["flow:0:full_buffer_SHA"])

    def test_wrong_source_content(self):
        value = flow()
        value["source_sha256"] = "different"
        self.assertFalse(self.validate(value)["flow:0:source_target_SHA"])

    def test_unsynchronized_completion(self):
        value = flow()
        value["completion"] = "enqueue"
        self.assertFalse(self.validate(value)["flow:0:completion_sync"])

    def test_IB_word_counter_and_payload(self):
        name = "/sys/class/infiniband/hca0/ports/1/counters/port_rcv_data"
        value = flow()
        value["clock_domain"] = "receiver/boot/monotonic"
        value["nic_snapshot_before"] = {"monotonic_ns": 5, "clock_domain": value["clock_domain"],
                                        "values": {name: {"unit_bytes": 4, "raw": 10}}}
        value["nic_snapshot_after"] = {"monotonic_ns": 115, "clock_domain": value["clock_domain"],
                                       "values": {name: {"unit_bytes": 4, "raw": 60}}}
        ports = [{"hca": "hca0", "port": 1}]
        bindings = [{"rank": r, "passed": True, "actual_hca_ports": ports} for r in (2, 3)]
        row = {"case_id": "network_single_a", "flows": [value],
               "actual_nic_payload_evidence": {"counter": name, "direction": "receive",
                                                "expected_payload_bytes": 200, "actual_counter_delta_bytes": 200}}
        audit = Audit()
        audit.nic(row, bindings)
        self.assertTrue(all(c["passed"] for c in audit.checks))
        bad = copy.deepcopy(row)
        bad["flows"][0]["nic_snapshot_after"]["values"][name]["raw"] = 11
        audit = Audit()
        audit.nic(bad, bindings)
        self.assertFalse(next(c["passed"] for c in audit.checks if c["check"].endswith("actual_port_receive_payload")))

    def test_wrong_port_and_cross_host_counter(self):
        name = "/sys/class/infiniband/hca0/ports/1/counters/port_rcv_data"
        value = flow()
        value["clock_domain"] = "receiver/boot/monotonic"
        value["nic_snapshot_before"] = {"monotonic_ns": 5, "clock_domain": value["clock_domain"],
                                        "values": {name: {"unit_bytes": 4, "raw": 10}}}
        value["nic_snapshot_after"] = {"monotonic_ns": 115, "clock_domain": "another_host/boot/monotonic",
                                       "values": {name: {"unit_bytes": 4, "raw": 60}}}
        bindings = [{"rank": r, "passed": True, "actual_hca_ports": [{"hca": "hca0", "port": 1}]} for r in (2, 3)]
        audit = Audit()
        audit.nic({"case_id": "network_single_a", "flows": [value]}, bindings)
        self.assertFalse(next(c["passed"] for c in audit.checks if c["check"].endswith("raw_actual_port_counter_valid")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
