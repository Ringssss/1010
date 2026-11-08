"""CPU checks for flow controls and shared-versus-split port accounting."""
import argparse
import json
from pathlib import Path
import tempfile
import unittest

import run_diagnosis as driver


def snapshot(time_ns, values):
    return {"clock_domain": "receiver/boot/monotonic_ns", "monotonic_ns": time_ns,
            "values": {f"/sys/class/infiniband/{hca}/ports/1/counters/port_rcv_data":
                       {"raw": raw, "unit_bytes": 4} for hca, raw in values.items()}}


def flow(fid, hca, before, after):
    return {"flow_id": fid, "dst": fid + 2, "bytes": 400,
        "clock_domain": "receiver/boot/monotonic_ns",
        "nic_snapshot_before": snapshot(before[0], {hca: before[1]}),
        "nic_snapshot_after": snapshot(after[0], {hca: after[1]}),
        "idle_background_counter_delta": {"snapshot_start_ns": 1, "snapshot_end_ns": 10,
            "deltas": [{"counter": f"/sys/class/infiniband/{hca}/ports/1/counters/port_rcv_data",
                         "valid": True, "delta_bytes": 0}]}}


class DiagnosisTests(unittest.TestCase):
    def bindings(self, first="shca_0", second="shca_0"):
        return [{"actual_hca_ports": [{"hca": hca, "port": 1}]}
                for hca in (first, second, first, second)]

    def test_shared_port_counted_once(self):
        flows = [flow(0, "shca_0", (20, 100), (40, 300)),
                 flow(1, "shca_0", (21, 100), (41, 300))]
        result = driver.per_port_payload(flows, self.bindings())
        self.assertTrue(result["passed"])
        self.assertEqual(result["expected_payload_bytes"], 800)
        self.assertEqual(result["actual_counter_delta_bytes"], 800)
        self.assertEqual(len(result["per_port"]), 1)

    def test_split_ports_counted_individually(self):
        flows = [flow(0, "shca_0", (20, 100), (40, 200)),
                 flow(1, "shca_1", (21, 200), (41, 300))]
        result = driver.per_port_payload(flows, self.bindings(second="shca_1"))
        self.assertTrue(result["passed"])
        self.assertEqual(result["actual_counter_delta_bytes"], 800)
        self.assertEqual([p["flow_ids"] for p in result["per_port"]], [[0], [1]])

    def test_wrong_actual_port_fails(self):
        flows = [flow(1, "shca_0", (20, 100), (40, 200))]
        result = driver.per_port_payload(flows, self.bindings(second="shca_1"))
        self.assertFalse(result["passed"])

    def test_both_ends_share_flow_control(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            state = {"nodes": {"node27": {"ip": "12.11.3.27"}, "node29": {}},
                     "env": {"HSA_FORCE_FINE_GRAIN_PCIE": "1"}}
            workers = [{"worker": rank, "node": "node27" if rank < 2 else "node29",
                        "gpu": rank % 2, "numa": 0 if rank % 2 == 0 else 3} for rank in range(4)]
            args = argparse.Namespace(port=33962, timeout=600, hca="shca_0",
                                      flow_b_hca="shca_1", ifname="ib0", flow_b_disable_gdr=False)
            entries = driver.process_specs(state, workers, out, args)
            for rank, row in enumerate(entries):
                spec = json.loads(Path(row["spec"]).read_text())
                expected = "shca_0" if rank % 2 == 0 else "shca_1"
                self.assertEqual(spec["env"]["NCCL_IB_HCA"], expected)
                self.assertEqual(spec["command"][spec["command"].index("--hca") + 1], expected)
                self.assertNotIn("NCCL_NET_GDR_LEVEL", spec["env"])
                self.assertEqual(spec["env"]["NCCL_SOCKET_IFNAME"], "ib0")
                self.assertEqual(spec["core_dump_limit"], 0)
                self.assertEqual(row["spec_sha256"], driver.inventory.digest(row["spec"]))

    def test_loc_only_affects_both_b_ends(self):
        with tempfile.TemporaryDirectory() as directory:
            state = {"nodes": {"node27": {"ip": "12.11.3.27"}, "node29": {}}, "env": {}}
            workers = [{"worker": rank, "node": "node27" if rank < 2 else "node29",
                        "gpu": rank % 2, "numa": 0 if rank % 2 == 0 else 3} for rank in range(4)]
            args = argparse.Namespace(port=33972, timeout=600, hca="shca_0", flow_b_hca="shca_1",
                                      ifname="ib0", flow_b_disable_gdr=True)
            entries = driver.process_specs(state, workers, Path(directory), args)
            for rank, row in enumerate(entries):
                spec = json.loads(Path(row["spec"]).read_text())
                self.assertEqual(spec["env"].get("NCCL_NET_GDR_LEVEL"), "LOC" if rank in (1, 3) else None)
                self.assertEqual(spec["env"]["NCCL_IB_HCA"], "shca_1" if rank in (1, 3) else "shca_0")

    def test_postexit_recovery_never_hides_real_failure(self):
        passed = {"measurement_status": "measured", "observations": [
            {"case_id": name} for name in ("network_single_a", "network_single_b", "network_pair")],
            **{key: {"passed": True} for key in ("cleanup", "before_after_preservation",
                                                  "code_preservation", "input_preservation")}}
        self.assertTrue(driver.can_recollect(passed))
        self.assertTrue(driver.can_recollect(dict(passed, error="actual receiving port payload evidence failed")))
        self.assertFalse(driver.can_recollect(dict(passed, error="RuntimeError('synthetic allocation SHA256 failed')")))
        self.assertFalse(driver.can_recollect(dict(passed, cleanup={"passed": False})))
        self.assertFalse(driver.can_recollect(dict(passed, observations=[])))


if __name__ == "__main__":
    unittest.main()
