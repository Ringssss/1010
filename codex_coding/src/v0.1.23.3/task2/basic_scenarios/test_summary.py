"""In-memory checks; no GPU, network, input mutation, or experiment result writes."""
import copy
import unittest
from summarize_basic import compare_protected, normalize_run, ordered_signature, render


class SummaryTests(unittest.TestCase):
    def fixture(self):
        op = {"op_id": 0, "target": "phaseA/rank0", "replica_id": "ownerA", "identity": "weights",
              "tp": 0, "off": 0, "n": 4096, "src_w": 0, "dst_w": 2, "kind": "network",
              "path": "0:dram:0->2", "source_offset": 0}
        case = {"phase": "phaseA", "method_id": "janus_host", "status": "passed", "correct": True,
                "primary_T_ready_s": 1.0, "verification": {"passed": True, "checked_bytes": 4096},
                "flow": {"plan": {"ops": [op]}, "execute_wall_s": .5}}
        return {"scenario_id": "S2", "status": "passed", "cases": [case], "cleanup_errors": []}

    def test_phase_and_id_do_not_change_signature(self):
        op = self.fixture()["cases"][0]["flow"]["plan"]["ops"][0]
        other = dict(op, op_id=30, target="phaseB/rank0", replica_id="ownerB")
        self.assertEqual(ordered_signature([op]), ordered_signature([other]))

    def test_tier_and_order_change_signature(self):
        op = self.fixture()["cases"][0]["flow"]["plan"]["ops"][0]
        other = dict(op, off=4096)
        self.assertNotEqual(ordered_signature([op, other]), ordered_signature([other, op]))
        self.assertNotEqual(ordered_signature([op]), ordered_signature([dict(op, path="0:hbm:0->2")]))
        self.assertNotEqual(ordered_signature([op], {"ownerA": "active"}),
                            ordered_signature([op], {"ownerA": "ipc"}))

    def test_identical_s2_decisions_have_no_algorithm_gain(self):
        data = self.fixture()
        second = copy.deepcopy(data["cases"][0]); second.update(phase="phaseB", method_id="resource_lp")
        data["cases"].append(second)
        result = normalize_run(data, "fixture")
        self.assertEqual(result["accepted_new_restore_count"], 2)
        self.assertEqual(result["equivalent_decisions"][0]["conclusion"], "same_ordered_decision_not_algorithm_gain")

    def test_failed_cleanup_retains_observation_without_acceptance(self):
        data = self.fixture(); data.update(status="failed", cleanup_errors=["uncertain detach"])
        result = normalize_run(data, "fixture")
        self.assertEqual(result["observed_new_restore_count"], 1)
        self.assertEqual(result["accepted_new_restore_count"], 0)
        self.assertEqual(result["status"], "failed")

    def test_partial_sha_rejected(self):
        data = self.fixture(); data["cases"][0]["verification"]["checked_bytes"] -= 1
        self.assertEqual(normalize_run(data, "fixture")["accepted_new_restore_count"], 0)

    def test_bootstrap_and_history_create_no_baseline_samples(self):
        for data in ({"status": "passed", "measurement_status": "bootstrap_only", "native_reference": {"text": "ok"}},
                     {"status": "passed", "evidence_class": "historical_reference", "new_samples": 0}):
            result = normalize_run(data, "fixture")
            self.assertEqual(result["accepted_new_restore_count"], 0)
            self.assertEqual(result["observed_new_restore_count"], 0)

    def test_s3_survival_is_not_continuous_interference(self):
        data = self.fixture(); data["scenario_id"] = "S3"
        result = normalize_run(data, "fixture")
        self.assertIn("no sustained interference", result["cases"][0]["foreground_evidence_scope"])

    def test_cpu_numeric_errors_render_and_retain_failed_status(self):
        run = normalize_run({"tests_run": 7, "errors": 1, "failures": 0, "status": "failed"}, "cpu_fixture")
        text = render({"runs": [run], "observed_new_restore_count": 0, "accepted_new_restore_count": 0})
        self.assertIn("failed", text)
        self.assertEqual(run["test_errors"], 1)

    def test_failure_record_preserved_without_samples(self):
        run = normalize_run({"status": "failed", "input_record": "failure.json", "error": "connection refused"}, "fixture")
        self.assertEqual(run["kind"], "failure_record")
        self.assertEqual(run["error"], "connection refused")
        self.assertEqual(run["observed_new_restore_count"], 0)

    def test_active_setup_and_shared_cumulative_cost_scope(self):
        data = self.fixture(); data.update(scenario_id="S3", active_setup={"wall_s": 86.95, "timing": {"load_s": .211}})
        data["cases"][0]["post_copy_assessment"] = {"active_source_evidence": [{"evidence": {"additional_lifetime_s": 61.8, "additional_hbm_byte_seconds": 1e13}}]}
        run = normalize_run(data, "fixture")
        self.assertEqual(run["active_setup"]["wall_s"], 86.95)
        self.assertIn("No independent per-method", run["active_cost_scope"])
        self.assertEqual(run["active_cost_snapshots"][0]["evidence"][0]["evidence"]["additional_lifetime_s"], 61.8)

    def test_real_target_scope_mapping(self):
        data = self.fixture()
        data.update(_target_slot="target_b", memory_samples=[{"workers": [{"worker": 0, "node": "node27"}, {"worker": 2, "node": "node29"}]}])
        scope = normalize_run(data, "fixture")["cases"][0]["runtime_scope"]
        self.assertEqual(scope["source_nodes"], ["node27"])
        self.assertEqual(scope["target_nodes"], ["node29"])
        self.assertEqual(scope["locality"], "cross_node")

    def test_protected_metadata_ignores_headroom_but_checks_leases(self):
        row = {"id": "source", "identity": "weights", "tp": 0, "off": 0, "n": 4096, "tier": "dram",
               "generation": 1, "state": "VALID", "leases": 0, "allocated_bytes": 4096, "epoch": "epoch", "worker": 8}
        first = {"endpoint_probes": [{"worker": 8, "status": "passed", "inventory": {"worker": 8, "epoch": "epoch", "session": "original", "node": "node28", "replicas": [row], "headroom_bytes": {"hbm": 10}}}]}
        second = copy.deepcopy(first)
        second["endpoint_probes"][0]["inventory"]["headroom_bytes"]["hbm"] = 20
        self.assertTrue(compare_protected(first, second)["metadata_unchanged"])
        second["endpoint_probes"][0]["inventory"]["replicas"][0]["leases"] = 1
        self.assertFalse(compare_protected(first, second)["metadata_unchanged"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
