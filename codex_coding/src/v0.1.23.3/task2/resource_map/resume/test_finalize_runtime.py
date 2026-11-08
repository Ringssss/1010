"""CPU failure-boundary checks; no positive runtime acceptance fixture is created."""
import json
import unittest
import finalize_runtime as final


class FailureDeltaTests(unittest.TestCase):
    def setUp(self):
        paths = {"original": "build_r04/resource_map.json", "control": "resume_control_r02/summary.json",
            "local": "local_xml_node27_r01/summary.json", "diagnosis": "local_xml_diagnosis_r01/summary.json",
            "control_audit": "resume_audit_control_r04/summary.json", "catalog_audit": "resume_audit_local_catalog_r02/summary.json"}
        self.values = {name: final.read(final.RESULTS / path) for name, path in paths.items()}
        self.values["references"] = [final.ref(final.RESULTS / path) for path in paths.values()]
        independent = final.RESULTS / "resume_audit_state_delta_r01/state_delta.json"
        self.values["independent_delta"] = final.read(independent)
        self.values["references"].append(final.ref(independent))
        self.values["events"] = [json.loads(line) for line in
            (final.RESULTS / "local_xml_diagnosis_r01/diagnosis.events.jsonl").read_text().splitlines()]

    def test_failure_delta_preserves_all_planning_inputs(self):
        result = final.derive(**self.values)
        original = self.values["original"]
        for key in ("physical", "availability", "measurements", "historical_profile_refs", "session_ref"):
            self.assertEqual(result[key], original[key])
        for node in ("node27", "node29"):
            config = result["runtime_configs"]["owned_xml/" + node]
            self.assertFalse(config["runtime_observed"])
            self.assertFalse(config["runtime_accepted"])
            self.assertEqual(config["observed_resource_ids"], [])
        self.assertFalse(result["planning_readiness"]["runtime_ready"])
        self.assertFalse(result["planning_readiness"]["performance_ready"])
        self.assertEqual(result["runtime_configs"]["owned_xml/node29"], original["runtime_configs"]["owned_xml/node29"])
        op = result["current_operational_status"]["nodes"]["node27"]
        self.assertEqual([r["sysfs_minus_keeper_bytes"] for r in op["free_metric_comparison"]], [34033664, 787984384])
        self.assertFalse(op["source_catalog_protection"]["physical_free_recovery_verified"])
        self.assertNotEqual(result["runtime_configs"]["owned_xml/node27"]["fingerprint"],
                            original["runtime_configs"]["owned_xml/node27"]["fingerprint"])

    def test_success_claim_is_rejected(self):
        self.values["local"]["runtime_accepted"] = True
        with self.assertRaisesRegex(ValueError, "unaccepted failed"):
            final.derive(**self.values)

    def test_started_rank_is_rejected(self):
        self.values["local"]["processes"][0]["start_reply"] = {"pid": 1}
        with self.assertRaisesRegex(ValueError, "zero started"):
            final.derive(**self.values)

    def test_payload_metadata_claim_is_rejected(self):
        self.values["diagnosis"]["gpu_payload_bytes"] = 1
        with self.assertRaisesRegex(ValueError, "metadata-only"):
            final.derive(**self.values)

    def test_false_hip_timeout_interpretation_is_rejected(self):
        self.values["events"].append({"phase": "gpu01_visible_hipInit_enter"})
        with self.assertRaisesRegex(ValueError, "diagnostic boundary"):
            final.derive(**self.values)

    def test_unpassed_catalog_audit_is_rejected(self):
        self.values["catalog_audit"]["status"] = "failed"
        with self.assertRaisesRegex(ValueError, "audit is not passed"):
            final.derive(**self.values)

    def test_independent_delta_cannot_upgrade_runtime(self):
        self.values["independent_delta"]["runtime_ready"] = True
        with self.assertRaisesRegex(ValueError, "readiness boundary"):
            final.derive(**self.values)


if __name__ == "__main__":
    unittest.main()
