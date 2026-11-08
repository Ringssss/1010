"""CPU checks for evidence identities, acceptance, and comparison boundaries."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from complete_table import (BASIC, COLUMNS, ROOT, Inputs, aliases_for, build, cell_label, cell_status,
                            comparable, comparisons, long_measurements, make_matrix,
                            normalize_case, timing_proof, write_outputs, control_comparisons)


def case_fixture():
    ops = [{"identity": "snapshot", "tp": 0, "off": 0, "n": 16, "src_w": 0, "dst_w": 1,
            "kind": "copy", "source_offset": 0, "path": "0:dram:1", "replica_id": "base"}]
    return {"phase": "restore", "method_id": "janus_host", "status": "passed", "correct": True,
            "primary_T_ready_s": 1.0, "flow": {"plan": {"ops": ops}, "verification": {"passed": True, "checked_bytes": 16}},
            "assessment": {"status": "applicable", "resource_ledger": {"allocations": [{"allocation_id": "base", "role": "base"}]}},
            "timing": {"load_s": 1.0, "ranks": {"0": {"events": [
                {"event": "load_begin", "host": "host", "monotonic_ns": 100},
                {"event": "weights_ready", "host": "host", "monotonic_ns": 1000000100}]}}}}


class CompleteTableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.inputs = Inputs(self.root)
        self.run = {"scenario_id": "S2", "run_id": "single", "status": "passed", "cleanup_errors": [],
                    "target_slot": "target_a", "budget": {"base": 16}, "format_contract": {"tp": 1}, "session_sha256": "frozen"}
        self.case = case_fixture()

    def tearDown(self):
        self.tmp.cleanup()

    def normalized(self):
        return normalize_case(self.inputs, "run/summary.json", self.run, self.case, 0)

    def test_missing_unmeasured_and_proven_infeasible_are_distinct(self):
        self.assertEqual(cell_status({}, [], missing=True), "missing")
        self.assertEqual(cell_status({"implementation": "implemented"}, []), "unmeasured")
        self.assertEqual(cell_status({"applicability": "infeasible", "reason": "budget", "source_files": ["evidence"]}, []), "infeasible")
        self.assertEqual(cell_status({"applicability": "infeasible"}, []), "unmeasured")

    def test_cold_load_unadapted_is_not_infeasible(self):
        scope = {"applicability": "conditional", "implementation": "not_adapted"}
        self.assertEqual(cell_status(scope, []), "not_adapted")

    def test_single_sample_is_not_multiplied_by_metric_rows(self):
        row = self.normalized()
        row["metrics"]["execute_s"] = 0.5
        self.assertEqual(len(long_measurements([row])), 2)
        cell = make_matrix([row], [])[1]["cells"]["janus_host"]
        self.assertEqual(cell["independent_samples"], 1)
        self.assertEqual(cell["new_samples"], 1)
        self.assertIn("n=1", cell_label(cell))

    def test_alias_preserves_source_and_adds_no_sample(self):
        row = self.normalized()
        run = {"route_aliases": [{"source_phase": "restore", "routing": "resource_lp", "reason": "same decision"}]}
        alias = aliases_for([row], run)[0]
        self.assertEqual(alias["alias_of"], row["sample_id"])
        self.assertEqual(alias["source_file"], row["source_file"])
        self.assertEqual(alias["new_sample_count"], 0)
        self.assertEqual(alias["independent_sample_count"], 0)
        self.assertFalse(comparable(row, alias))

    def test_missing_alias_source_is_rejected(self):
        with self.assertRaises(ValueError):
            aliases_for([], {"route_aliases": [{"source_phase": "absent"}]})

    def test_historical_reference_has_zero_new_samples(self):
        row = normalize_case(self.inputs, "old/summary.json", self.run, self.case, 0, True)
        self.assertEqual(row["measurement_status"], "historical_reference")
        self.assertEqual(row["independent_sample_count"], 1)
        self.assertEqual(row["new_sample_count"], 0)

    def test_same_operations_never_report_algorithm_gain(self):
        a = self.normalized()
        b = copy.deepcopy(a)
        b.update(method_id="resource_lp", sample_id="different")
        b["metrics"]["T_ready_s"] = 0.8
        comparison = comparisons([a, b])[0]
        self.assertFalse(comparison["algorithm_gain"])
        self.assertEqual(comparison["conclusion"], "same_ordered_decision_not_algorithm_gain")
        self.assertNotIn("speedup", comparison)

    def test_each_fairness_variable_blocks_cross_configuration_comparison(self):
        a = self.normalized()
        b = copy.deepcopy(a)
        self.assertTrue(comparable(a, b))
        changes = {"target": {"slot": "other"}, "budget": {"base": 17}, "format_contract": {"tp": 2},
                   "logical_bytes": 17, "legal_opportunity_sha256": "other", "executor": "parallel",
                   "matrix_row": "S3", "comparison_group": "other"}
        for field, changed in changes.items():
            with self.subTest(field=field):
                b = copy.deepcopy(a)
                b[field] = changed
                self.assertFalse(comparable(a, b))

    def test_unknown_comparison_condition_cannot_be_invented(self):
        a = self.normalized()
        b = copy.deepcopy(a)
        a["budget"] = b["budget"] = None
        self.assertFalse(comparable(a, b))

    def test_full_sha_text_and_cleanup_are_required(self):
        self.assertTrue(self.normalized()["accepted"])
        self.case["flow"]["verification"]["checked_bytes"] = 15
        self.assertFalse(self.normalized()["accepted"])
        self.case = case_fixture()
        self.case["correct"] = False
        self.assertFalse(self.normalized()["accepted"])
        self.case = case_fixture()
        self.run["cleanup_errors"] = ["not detached"]
        self.assertFalse(self.normalized()["accepted"])

    def test_cross_host_clock_is_rejected(self):
        self.case["timing"]["ranks"]["0"]["events"][1]["host"] = "other"
        self.assertFalse(timing_proof(self.case)["same_host_clock_verified"])
        self.assertFalse(self.normalized()["accepted"])

    def test_main_table_does_not_mix_history_into_new_numeric_cell(self):
        new = self.normalized()
        old = copy.deepcopy(new)
        old.update(sample_id="old", measurement_status="historical_reference", new_sample_count=0)
        old["metrics"]["T_ready_s"] = 9
        cell = make_matrix([old, new], [])[1]["cells"]["janus_host"]
        self.assertEqual(cell_label(cell), "新 1.000000s (n=1)")

    def test_matrix_has_all_baselines_and_preserves_original_mechanism_split(self):
        matrix = make_matrix([self.normalized()], [])
        self.assertEqual(len(matrix), 5)
        self.assertEqual(set(matrix[1]["cells"]), set(COLUMNS))
        self.assertEqual(matrix[1]["cells"]["janus_host"]["status"], "new_measurement")
        self.assertEqual(matrix[1]["cells"]["janus_original"]["status"], "not_adapted")

    def test_s5_control_is_independent_matrix_row(self):
        self.run.update(scenario_id="S5", variant="software_worker_serial")
        self.case.update(method_id="fastest", row_id="target_major")
        row = self.normalized()
        matrix = make_matrix([row], [])
        self.assertEqual(matrix[-1]["row_id"], "S5/software_worker_serial/target_major")
        self.assertEqual(matrix[4]["cells"]["fastest"]["status"], "unmeasured")
        self.assertEqual(matrix[-1]["cells"]["resource_lp"]["status"], "unmeasured")

    def test_s4_variant_stays_on_s4_row_and_preserves_shared_budget_scope(self):
        self.run.update(scenario_id="S4", variant="fragmented_cache", protocol={"janus_host_budget_scope": "shared fixture only"})
        self.case.update(shared_cache_budget_bytes=8 << 30, selected_source_bytes={"hbm": 0, "dram": 16})
        row = self.normalized()
        self.assertEqual(row["matrix_row"], "S4")
        self.assertEqual(row["scenario_variant"], "fragmented_cache")
        self.assertEqual(row["shared_cache_budget_bytes"], 8 << 30)
        self.assertEqual(row["selected_source_bytes"]["hbm"], 0)
        self.assertEqual(row["cache_budget_scope"], "shared fixture only")

    def test_accepted_observation_requires_complete_tp_rank_window(self):
        op = copy.deepcopy(self.case["flow"]["plan"]["ops"][0])
        op["tp"] = 1
        self.case["flow"]["plan"]["ops"].append(op)
        self.case["flow"]["verification"]["checked_bytes"] = 32
        row = self.normalized()
        self.assertFalse(row["accepted"])
        self.assertFalse(row["proof"]["clock"]["complete_rank_window"])

    def test_software_controls_require_matched_sources_layout_and_runtime_proof(self):
        self.run.update(scenario_id="S5", variant="software_worker_serial", planned_cache_layout={"worker": 0, "bytes": 8})
        self.case.update(method_id="fastest", row_id="target_major", control_type="execution_order",
                         physical_resource_gate="not_measured", software_runtime_order_verification={"passed": True})
        a = self.normalized()
        b = copy.deepcopy(a)
        b.update(matrix_row="S5/software_worker_serial/rank_interleaved", label="interleaved", case_index=1, sample_id="b")
        b["metrics"]["T_ready_s"] = 0.5
        rows = control_comparisons([a, b])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["observed_T_ready_difference_s"], 0.5)
        self.assertFalse(rows[0]["algorithm_gain"])
        reverse = control_comparisons([b, a])[0]
        self.assertEqual(reverse["reference_control"], "target_major")
        self.assertEqual(reverse["observed_saved_s"], 0.5)
        self.assertEqual(reverse["observed_saved_percent"], 50.0)
        for field, value in {"target": {"slot": "other"}, "budget": {"base": 17},
                             "selected_source_bytes": {"dram": 15}, "source_bytes_by_worker": {"0": 15},
                             "cache_layout": {"worker": 2, "bytes": 8}, "operation_content_sha256": "different"}.items():
            changed = copy.deepcopy(b)
            changed[field] = value
            self.assertEqual(control_comparisons([a, changed]), [], field)
        b["proof"]["software_runtime_order_verification"]["passed"] = False
        self.assertEqual(control_comparisons([a, b]), [])

    def test_new_ipc_hits_schema_is_supported_without_reclassifying_owner_setup(self):
        source = self.root / "ipc/summary.json"
        source.parent.mkdir()
        hit = copy.deepcopy(self.case)
        hit.pop("method_id")
        hit.pop("flow")
        hit["exports"] = [{"nbytes": 16}]
        data = {"run_id": "new_ipc", "status": "passed", "hits": [hit],
                "owner_setup": {"verification": {"passed": True, "checked_bytes": 16}}}
        source.write_text(json.dumps(data))
        summary, _ = build(self.root, [source], include_history=False)
        self.assertEqual(summary["counts"]["unique_new_observations"], 1)
        self.assertEqual(summary["counts"]["unique_accepted_new_observations"], 1)
        self.assertEqual(summary["observations"][0]["method_id"], "sglang_ipc_hip")
        proof = summary["observations"][0]["proof"]
        self.assertTrue(proof["full_sha_passed"])
        self.assertTrue(proof["owner_integrity_sha_passed"])
        self.assertEqual(proof["verification_scope"], "IPC_owner_establishment")
        self.assertFalse(proof["per_hit_rehash"])

    def test_existing_output_is_not_overwritten(self):
        summary, manifest = build(self.root, [], include_history=False)
        out = self.root / "table"
        write_outputs(summary, manifest, out)
        with self.assertRaises(FileExistsError):
            write_outputs(summary, manifest, out)

    def test_missing_run_is_a_gap_not_a_runtime_failure(self):
        summary, _ = build(self.root, ["absent"], include_history=False)
        self.assertEqual(summary["missing_inputs"], ["absent/summary.json"])
        self.assertEqual(summary["counts"]["unique_new_observations"], 0)

    @unittest.skipUnless((ROOT / BASIC / "s3_r01/summary.json").is_file(), "repository GPU results unavailable")
    def test_real_saved_results_retain_counts_and_comparison_scope(self):
        summary, manifest = build(ROOT, [BASIC / "s2_r01", BASIC / "s3_r01"],
                                  BASIC / "baseline_scope_r01/review_r02/summary.json",
                                  decision_audit=BASIC / "table_cpu_r01/decision_audit.json")
        self.assertEqual(summary["counts"]["unique_independent_historical_observations"], 16)
        self.assertEqual(summary["counts"]["unique_new_observations"], 4)
        self.assertEqual(summary["counts"]["unique_accepted_new_observations"], 4)
        self.assertEqual(summary["missing_inputs"], [])
        self.assertEqual(len(summary["comparisons"]), 2)
        self.assertTrue(all(x["same_ordered_decision"] for x in summary["comparisons"]))
        self.assertEqual(summary["matrix"][1]["cells"]["fastest"]["status"], "decision_only_alias")
        self.assertEqual(summary["matrix"][2]["cells"]["fastest"]["status"], "decision_only_alias")
        self.assertTrue(manifest)

    @unittest.skipUnless((ROOT / BASIC / "s5_r01/summary.json").is_file(), "new scenario results unavailable")
    def test_new_s1_s4_s5_schema_and_software_control_scope(self):
        summary, _ = build(ROOT, [BASIC / name for name in ("s1_r01", "s4_r01", "s5_r01")],
                           BASIC / "baseline_scope_r01/review_r02/summary.json", include_history=False)
        self.assertEqual(summary["counts"]["unique_new_observations"], 7)
        self.assertEqual(summary["counts"]["unique_accepted_new_observations"], 7)
        self.assertEqual(summary["matrix"][3]["cells"]["janus_host"]["status"], "new_measurement")
        self.assertEqual(len(summary["control_comparisons"]), 1)
        self.assertEqual(summary["control_comparisons"][0]["physical_resource_gate"], "not_measured")

    @unittest.skipUnless((ROOT / BASIC / "s5_r02/summary.json").is_file(), "reverse scenario results unavailable")
    def test_final_thirteen_observations_with_null_lp_decision_alias(self):
        summary, _ = build(ROOT, [BASIC / name for name in ("s1_r01", "s2_r01", "s3_r01", "s4_r01", "s5_r01", "s5_r02")],
                           BASIC / "baseline_scope_r01/review_r02/summary.json",
                           decision_audit=[BASIC / "table_cpu_r01/decision_audit.json", BASIC / "table_cpu_r01/s5_lp_r01/decision_audit.json"],
                           supporting_sources=[BASIC / "stage2_final_fixture_r01", BASIC / "stage2_protected_after_r01"])
        self.assertEqual(summary["counts"]["unique_new_observations"], 13)
        self.assertEqual(summary["counts"]["unique_accepted_new_observations"], 13)
        self.assertEqual(summary["counts"]["unique_independent_historical_observations"], 16)
        self.assertEqual(len(summary["control_comparisons"]), 2)
        self.assertTrue(summary["protected_metadata_check"]["metadata_unchanged"])
        physical = next(x for x in summary["matrix"] if x["row_id"] == "S5")
        self.assertTrue(all(x["status"] == "unmeasured" and x["independent_samples"] == 0 for x in physical["cells"].values()))
        self.assertEqual(len(summary["historical_software_evidence"]), 4)
        self.assertTrue(all(x["observed_saved_s"] > 0 for x in summary["control_comparisons"]))
        major = next(x for x in summary["matrix"] if x["row_id"].endswith("fastest_target_major"))
        self.assertEqual(major["cells"]["resource_lp"]["status"], "decision_only_alias")
        self.assertEqual(major["cells"]["resource_lp"]["T_ready"], [])
        self.assertEqual(major["cells"]["fastest"]["accepted_new_samples"], 2)


if __name__ == "__main__":
    unittest.main()
