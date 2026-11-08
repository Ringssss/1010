"""CPU regression checks for evidence acceptance and prior-reference boundaries."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from summarize_loading import (BASE, PREVIOUS, ROOT, Inputs, build, clock_proof, cleanup_proof,
                               discover, performance_row, write_outputs)


def timing(host="node29"):
    return {"load_s": 1.0, "ranks": {str(rank): {"events": [
        {"event": "load_begin", "monotonic_ns": 100, "host": host},
        {"event": "weights_ready", "monotonic_ns": 1000000100, "host": host}]} for rank in range(2)}}


def blitz_fixture():
    ops = [{"op_id": rank, "tp": rank, "n": 35164859056} for rank in range(2)]
    case = {"phase": "restore", "status": "passed", "correct": True, "primary_T_ready_s": 1.0, "timing": timing(),
            "flow": {"plan": {"ops": ops}}, "verification": {"passed": True, "checked_bytes": 70329718112,
                "operations": {str(rank): {"n": 35164859056, "source_sha256": str(rank)*64, "target_sha256": str(rank)*64} for rank in range(2)}}}
    run = {"run_id": "blitz_fixture", "scenario_id": "S3", "status": "passed", "cases": [case],
           "cleanup_errors": [], "remaining_owned_ids": [], "retained_base_unchanged": True, "protected_catalog_unchanged": True}
    return run, case


class LoadingSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.inputs = Inputs(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def previous(self):
        reference = {"sample_id": "prior#hit", "scenario_id": "S1", "method_id": "sglang_ipc_hip", "run_id": "old",
            "measurement_status": "new_measurement", "accepted": True, "new_sample_count": 1, "metrics": {"T_ready_s": .2},
            "source_file": "prior.json", "matrix_row": "S1", "proof": {"per_hit_rehash": False}}
        path = self.root / "previous.json"
        path.write_text(json.dumps({"audit_kind": "prior", "observations": [reference], "matrix": [{"row_id": "S1", "cells": {"sglang_original": "not_adapted"}}], "counts": {"unique_new_observations": 1}}))
        return path

    def write_attempt(self, name, data):
        path = self.root / name / "summary.json"
        path.parent.mkdir()
        path.write_text(json.dumps(data))
        return path

    def test_full_sha_text_clock_cleanup_are_required(self):
        run, case = blitz_fixture()
        row = performance_row(self.inputs, "blitz_fixture/summary.json", run, case, "blitz_scale")
        self.assertTrue(row["accepted"])
        for variable in ("correct", "sha", "cleanup", "status"):
            altered_run, altered_case = copy.deepcopy(run), copy.deepcopy(case)
            if variable == "correct": altered_case["correct"] = False
            if variable == "sha": altered_case["verification"]["operations"]["1"]["target_sha256"] = "wrong"
            if variable == "cleanup": altered_run["pending_resume_endpoints"] = ["http://pending"]
            if variable == "status": altered_run["status"] = "running"
            row = performance_row(self.inputs, "blitz_fixture/summary.json", altered_run, altered_case, "blitz_scale")
            self.assertFalse(row["accepted"], variable)
            self.assertIsNone(row["metrics"]["T_ready_s"])
            self.assertEqual(row["metrics"]["observed_T_ready_s"], 1.0)

    def test_store_full_requires_frozen_sha_and_final_lifetime_cleanup(self):
        run = {"status": "passed", "mode": "full", "primary_T_ready_s": 1.0, "timing": timing(), "text_correct": True,
            "worker_exit_code": 0, "code_unchanged": True, "protected_metadata_unchanged": True, "physical_hbm_reclaimed": True,
            "cleanup": {"container_removed": True}, "frozen_rank_sha256": {str(rank): str(rank)*64 for rank in range(2)},
            "worker_result": {"status": "passed", "ranks": [{"tp_rank": rank, "logical_bytes": 35164859056,
                "source_sha256": str(rank)*64, "target_sha256": str(rank)*64, "full_sha256_equal": True} for rank in range(2)]}}
        row = performance_row(self.inputs, "serverless_fixture/summary.json", run, run, "serverless_store_hip")
        self.assertTrue(row["accepted"])
        self.assertEqual(row["cache"]["label"], "prepared_newly_written_runtime_file_residency_unmeasured")
        run["frozen_rank_sha256"]["1"] = "other"
        self.assertFalse(performance_row(self.inputs, "serverless_fixture/summary.json", run, run, "serverless_store_hip")["accepted"])

    def test_cross_host_clock_and_missing_rank_rejected(self):
        value = timing()
        value["ranks"]["1"]["events"][1]["host"] = "other"
        self.assertFalse(clock_proof(value, 1.0)["passed"])
        self.assertFalse(clock_proof({"ranks": {"0": timing()["ranks"]["0"]}}, 1.0)["passed"])

    def test_duplicate_boundary_or_reversed_time_rejected(self):
        value = timing()
        value["ranks"]["0"]["events"].append(copy.deepcopy(value["ranks"]["0"]["events"][0]))
        self.assertFalse(clock_proof(value, 1.0)["passed"])
        value = timing()
        value["ranks"]["1"]["events"][1]["monotonic_ns"] = 1
        self.assertFalse(clock_proof(value, 1.0)["passed"])

    def test_capability_gate_never_becomes_model_performance(self):
        path = self.write_attempt("serverless_gate_fixture", {"status": "passed", "mode": "gate", "worker_result": {"total_logical_bytes": 24576,
            "loader": {"store_load_s": .05}, "status": "passed"}})
        data = build(self.inputs, [path], self.previous())
        self.assertEqual(data["counts"]["accepted_new_performance_samples"], 0)
        self.assertEqual(data["counts"]["passed_capability_gates"], 1)
        self.assertEqual(data["attempts"][0]["gate_bytes"], 24576)

    def test_functional_recovery_is_setup_not_store_performance(self):
        path = self.write_attempt("serverless_recovery_fixture", {"status": "passed", "scope": "target-only functional recovery; not a Store performance sample",
            "functional_setup_s": 180.0, "session_overlay": "overlay.json", "functional_restore": {"verification": {"passed": True}}})
        data = build(self.inputs, [path], self.previous())
        self.assertEqual(data["counts"]["accepted_new_performance_samples"], 0)
        self.assertEqual(data["attempts"][0]["kind"], "functional_setup")
        self.assertEqual(data["attempts"][0]["functional_setup_s"], 180.0)

    def test_hash_probes_are_diagnostics_not_model_observations(self):
        paths = [self.write_attempt("serverless_hash_current_fixture", {"status": "failed", "worker_exit_code": 139}),
            self.write_attempt("serverless_hash_set_device_fixture", {"status": "passed", "worker_exit_code": 0,
                "worker_result": {"status": "passed", "source_sha256": "same", "target_sha256": "same", "store": {"store_load_s": .05}}})]
        data = build(self.inputs, paths, self.previous())
        self.assertEqual(data["counts"]["accepted_new_performance_samples"], 0)
        self.assertTrue(all(row["kind"] == "diagnostic_non_performance" for row in data["attempts"]))
        self.assertEqual(data["attempts"][0]["error"], "worker_exit_code=139")

    def test_running_full_without_timing_is_pending(self):
        path = self.write_attempt("serverless_full_fixture", {"status": "running", "mode": "full"})
        data = build(self.inputs, [path], self.previous())
        self.assertEqual(data["status"], "snapshot_with_pending")
        self.assertEqual(data["counts"]["accepted_new_performance_samples"], 0)

    def test_failed_pending_resume_remains_pending_recovery(self):
        path = self.write_attempt("serverless_failed_fixture", {"status": "failed", "mode": "full",
            "cleanup": {"pending_resume_preserved": True}, "error": "RPC closed"})
        data = build(self.inputs, [path], self.previous())
        self.assertEqual(data["status"], "snapshot_with_pending")
        self.assertEqual(data["counts"]["pending_recovery_runs"], ["serverless_failed_fixture"])
        self.assertEqual(data["counts"]["failed_attempts"], 1)

    def test_separate_verified_cleanup_does_not_erase_original_pending(self):
        path = self.write_attempt("serverless_failed_fixture", {"status": "failed", "mode": "full", "container": "owned-container",
            "cleanup": {"pending_resume_preserved": True}, "error": "RPC closed"})
        directory = self.root / "serverless_fixture_cleanup"
        directory.mkdir()
        (directory / "identity_before.txt").write_text('{"fluidckpt.serverless_run":"serverless_failed_fixture"} {}\nPID COMMAND\nowned adapter\nPID COMMAND\npython -m fluidckpt.cluster.worker\n')
        (directory / "remove.txt").write_text("owned-container\n")
        data = build(self.inputs, [path], self.previous())
        self.assertEqual(data["counts"]["pending_recovery_runs"], [])
        self.assertTrue(data["attempts"][0]["cleanup"]["pending_resume_preserved"])
        self.assertTrue(data["recovery_supplements"][0]["cleanup_verified"])
        self.assertEqual(data["counts"]["accepted_new_performance_samples"], 0)

    def test_prior_new_observations_are_history_for_this_export(self):
        data = build(self.inputs, [], self.previous())
        reference = data["prior_references"][0]
        self.assertEqual(reference["original_measurement_status"], "new_measurement")
        self.assertEqual(reference["measurement_status"], "prior_reference")
        self.assertEqual(reference["new_sample_count"], 0)
        self.assertFalse(reference["proof"]["per_hit_rehash"])
        self.assertEqual(data["previous_complete_table"]["matrix"][0]["cells"]["sglang_original"], "not_adapted")

    def test_failures_and_source_identity_are_preserved(self):
        path = self.write_attempt("native_failure", {"status": "failed", "error": "cache gate rejected"})
        data = build(self.inputs, [path], self.previous())
        self.assertEqual(data["counts"]["failed_attempts"], 1)
        self.assertEqual(data["attempts"][0]["error"], "cache gate rejected")
        self.assertEqual(data["attempts"][0]["method_id"], "sglang_vendor_native")

    def test_no_speedup_is_derived_even_with_measured_baselines(self):
        run, _ = blitz_fixture()
        path = self.write_attempt("blitz_fixture", run)
        data = build(self.inputs, [path], self.previous())
        self.assertEqual(data["algorithm_speedups"], [])
        self.assertEqual(data["comparisons"], [])

    def test_reader_detects_mutation_of_source(self):
        path = self.previous()
        self.inputs.read(path)
        path.write_text("{}")
        with self.assertRaises(RuntimeError): self.inputs.verify()

    def test_output_scope_is_fixed(self):
        with self.assertRaises(ValueError): write_outputs({}, self.inputs, self.root / "outside")

    def test_actual_native_and_blitz_completed_results_accepted(self):
        inputs = Inputs(ROOT)
        paths = [BASE / "native_observed_r01/summary.json", BASE / "blitz_s3_r02/summary.json"]
        data = build(inputs, paths, PREVIOUS)
        self.assertEqual(data["counts"]["accepted_new_performance_samples"], 2)
        values = {row["method_id"]: row["metrics"]["T_ready_s"] for row in data["measurements"]}
        self.assertEqual(values["sglang_vendor_native"], 122.266963355)
        self.assertEqual(values["blitz_scale"], 2.804378593)
        rows = {row["method_id"]: row for row in data["measurements"]}
        self.assertEqual(rows["blitz_scale"]["setup"]["owner_build_s"], 115.05676756985486)
        self.assertEqual(rows["blitz_scale"]["setup"]["owner_copy_s"], 0.8167375205084682)
        self.assertEqual(rows["blitz_scale"]["setup"]["source_active_owner_setup_s"], 121.4236498195678)
        self.assertNotIn("source_active_owner_build", rows["blitz_scale"]["setup"])
        self.assertIn("includes independent post-ready full SHA", rows["sglang_vendor_native"]["metric_scopes"]["request_to_first_correct_token_s"])
        self.assertEqual(rows["sglang_vendor_native"]["setup"]["fresh_start_to_model_info_s"], 326.50008674524724)
        self.assertTrue(all(row["new_sample_count"] == 0 for row in data["prior_references"]))
        inputs.verify()

    def test_known_unavailable_set_is_compared_instead_of_failed_scan_label(self):
        paths = []
        for name in ("stage2_protected_after_r01", "baseline_loading_protected_after_r01"):
            directory = self.root / name
            directory.mkdir()
            path = directory / "summary.json"
            path.write_text(json.dumps({"status": "failed", "read_only": True,
                "endpoint_probes": [{"status": "failed", "worker": 0}]}))
            paths.append(path)
        data = build(self.inputs, [], self.previous(), supporting_paths=paths)
        comparison = data["supporting_evidence"]["protected_comparison"]
        self.assertTrue(comparison["metadata_unchanged"])
        self.assertTrue(comparison["unavailable_set_unchanged"])
        self.assertEqual(comparison["after_unavailable_workers"], [0])


if __name__ == "__main__":
    unittest.main()
