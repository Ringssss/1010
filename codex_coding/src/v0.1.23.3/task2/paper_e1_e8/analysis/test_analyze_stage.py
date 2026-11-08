"""CPU-only checks against false completion, false speedup and duplicate samples."""
import json
from pathlib import Path
import tempfile
import unittest

import analyze_stage as analysis


def inventory():
    return [{"worker": 0, "node": "node27", "gpu": 0, "hbm_allocated_bytes": 64,
             "hbm_reserved_bytes": 64, "headroom_bytes": {"hbm": 1024}, "used_bytes": {"hbm": 0}}]


def build(label, gib):
    return {"label": label, "budget_bytes": gib << 30, "cache_page_rounded_bytes": gib << 30,
            "base_page_rounded_bytes": 4096, "base_logical_bytes": 4000}


def case(label, scenario, routing, seconds):
    return {"label": label, "scenario": scenario, "routing": routing, "status": "passed",
            "phase": f"{label}_{scenario}_{routing}",
            "timing": {"load_s": seconds}, "correct": True, "verification": {"passed": True}}


def same_solutions():
    policy = {"selected": [], "routing": {route: {"scenario_plans": {s: {"ops": []}
        for s in ("target_a", "target_b")}} for route in ("fastest", "resource_lp")}}
    return {"policies": {"greedy": policy, "joint": policy}, "layout_signatures_equal": True}


class AnalyzerTests(unittest.TestCase):
    def test_negative_and_zero_improvements_preserved(self):
        self.assertEqual(analysis.delta(2, 3)["improvement_pct"], -50)
        self.assertEqual(analysis.delta(2, 2)["saved_s"], 0)

    def test_layout_identity_ignores_segmentation(self):
        row = {"tp": 0, "worker": 1, "tier": "hbm", "off": 0, "n": 10}
        split = [dict(row, n=5), dict(row, off=5, n=5)]
        self.assertEqual(analysis.layout_signature([row]), analysis.layout_signature(split))

    def test_running_e3_is_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            result = analysis.matrix_analysis(Path(directory), {"stage": "e3", "status": "running", "cases": [], "builds": []})
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["missing"]), 4)
        self.assertNotEqual(result["scientific_status"], "exploratory_primary_metric_evidence")

    def test_budget_mismatch_blocks_speedup_claim(self):
        rows = []
        for scenario in ("target_a", "target_b"):
            for label, budget, elapsed in (("a", 10, 2), ("b", 20, 1)):
                rows.append({"scenario": scenario, "label": label, "routing": "fastest", "measurement_valid": True,
                             "budget_bytes": budget, "base_page_rounded_bytes": 100,
                             "cache_page_rounded_bytes": budget, "T_ready_s": elapsed})
        result = analysis.paired_comparison(rows, "a", "fastest", "b", "fastest", "unequal")
        self.assertFalse(result["complete"])
        self.assertIsNone(result["equal_weight_mean"]["saved_s"])

    def test_e6_dedup_keeps_origin_and_negative_gain(self):
        data = {"stage": "e6", "status": "passed", "protocol": {"budgets_gib": "0,8",
                "baseline_routing": "fastest", "candidate_routing": "fastest"},
                "builds": [build("0gib_greedy", 0), build("8gib_greedy", 8)],
                "deduplicated": [{"budget_gib": 0}, {"budget_gib": 8}],
                "cases": [case(label, scenario, "fastest", seconds)
                    for label, seconds in (("0gib_greedy", 2), ("8gib_greedy", 3))
                    for scenario in ("target_a", "target_b")],
                "initial_inventory": inventory(), "final_inventory": inventory(),
                "final_slots": {"target_a": {"parked": True}, "target_b": {"parked": True}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for gib in (0, 8):
                (path / f"budget_{gib << 30}_solutions.json").write_text(json.dumps(same_solutions()))
            result = analysis.matrix_analysis(path, data)
        self.assertTrue(result["complete"])
        self.assertEqual(result["valid_measurements"], 4)
        self.assertTrue(result["zero_budget_origin_present"])
        self.assertEqual(result["budget_curve"][1]["delta_from_zero_budget"]["improvement_pct"], -50)
        self.assertIn("8gib_joint", result["budget_curve"][1]["aliases"])

    def test_e5_distinct_routing_expected(self):
        data = {"stage": "e5", "status": "passed", "protocol": {"budget_gib": 8,
                "baseline_routing": "fastest", "candidate_routing": "resource_lp"},
                "builds": [build("8gib_greedy", 8), build("8gib_joint", 8)],
                "cases": [case(label, scenario, routing, seconds)
                    for label, routing, seconds in (("8gib_greedy", "fastest", 2), ("8gib_joint", "resource_lp", 1.5))
                    for scenario in ("target_a", "target_b")],
                "initial_inventory": inventory(), "final_inventory": inventory(),
                "final_slots": {"target_a": {"parked": True}, "target_b": {"parked": True}}}
        with tempfile.TemporaryDirectory() as directory:
            result = analysis.matrix_analysis(Path(directory), data)
        self.assertTrue(result["complete"])
        self.assertEqual(result["comparisons"][0]["equal_weight_mean"]["improvement_pct"], 25)

    def test_ipc_generation_pass_does_not_imply_reclamation(self):
        data = {"status": "failed", "model": "A", "hits": [
            {"repetition": i, "status": "passed", "correct": True,
             "timing": {"per_host": {"a": {"load_s": 1}, "b": {"load_s": 2}}},
             "request_to_ready_ack_s": 3} for i in range(2)],
             "owner_setup": {"verification": {"passed": True}}}
        with tempfile.TemporaryDirectory() as directory:
            result = analysis.ipc_analysis(Path(directory), data)
        self.assertTrue(result["generation_passed"])
        self.assertFalse(result["owner_reclamation_passed"])
        self.assertFalse(result["gate_passed"])
        self.assertIsNone(result["hits"][0]["primary_T_ready_s"])

    def test_route_alias_is_not_new_timing_sample(self):
        rows = [dict(case("8gib_greedy", s, "fastest", 2), phase=s) for s in ("target_a", "target_b")]
        data = {"stage": "e4", "status": "passed", "protocol": {"budget_gib": 8},
                "builds": [build("8gib_greedy", 8)], "cases": rows, "deduplicated": [{"budget_gib": 8}],
                "route_aliases": [{"label": "8gib_greedy", "policy": "greedy", "routing": "resource_lp", "scenario": s,
                                   "source_phase": s, "new_observation": False} for s in ("target_a", "target_b")],
                "initial_inventory": inventory(), "final_inventory": inventory(),
                "final_slots": {"target_a": {"parked": True}, "target_b": {"parked": True}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / f"budget_{8 << 30}_solutions.json").write_text(json.dumps(same_solutions()))
            result = analysis.matrix_analysis(path, data)
        self.assertTrue(result["complete"])
        self.assertEqual(result["valid_measurements"], 2)
        self.assertEqual(result["alias_count"], 6)
        self.assertIsNone(result["comparisons"][-1]["equal_weight_mean"]["improvement_pct"])
        self.assertFalse(result["comparisons"][-1]["performance_delta_identified"])
        self.assertEqual(analysis.candidate_mean_display(result["comparisons"][-1]), "引用同一phase；未独立计时")

    def test_decision_only_verifies_reference_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, new = root / "old", root / "new"
            old.mkdir()
            new.mkdir()
            rows = [dict(case("8gib_greedy", s, "fastest", 2), phase=s) for s in ("target_a", "target_b")]
            previous = {"stage": "e4", "status": "passed", "cases": rows, "code_sha256": {"main_lib/test.py": "abc"}}
            (old / "summary.json").write_text(json.dumps(previous))
            for path in (old, new):
                (path / f"budget_{8 << 30}_solutions.json").write_text(json.dumps(same_solutions()))
                (path / f"budget_{8 << 30}_case.json").write_text(json.dumps({
                    "profile": {}, "tier_budgets": {"hbm": 8 << 30}, "base": [], "workers": []}))
            data = {"stage": "e5", "status": "decision_only", "decision": "unchanged", "new_gpu_restore_count": 0,
                    "cases": [], "speedup_claim": None, "code_sha256": {"main_lib/test.py": "abc"},
                    "protocol": {"budget_gib": 8, "baseline_routing": "fastest", "candidate_routing": "resource_lp"},
                    "comparison": {"run": str(old), "summary_sha256": analysis.sha(old / "summary.json"),
                        "samples": [{"policy": p, "routing": "fastest" if p == "greedy" else "resource_lp",
                                     "scenario": s, "source_phase": s, "T_ready_s": 2}
                                    for p in ("greedy", "joint") for s in ("target_a", "target_b")]}}
            result = analysis.decision_analysis(new, data)
            self.assertEqual(result["scientific_status"], "no_decision_increment")
            self.assertEqual(result["new_gpu_restore_count"], 0)
            self.assertEqual(result["performance_status"], "not_measured")
            self.assertEqual(result["decision_status"], "unchanged")
            self.assertIsNone(result["speedup_claim"])
            (old / "summary.json").write_text(json.dumps(dict(previous, status="failed")))
            changed = analysis.decision_analysis(new, data)
            self.assertFalse(changed["complete"])

    def test_recorded_e4_e5_read_only_regression(self):
        root = next(p for p in Path(__file__).resolve().parents if (p / "main_lib/fluidckpt").is_dir())
        results = root / "codex_coding/results/v0.1.23.3/task2/paper_e1_e8"
        e4 = results / "e4/e4_c01_qwen_r01"
        e5 = results / "e5/e5_c01_qwen_decision_r01"
        if not (e4 / "summary.json").exists() or not (e5 / "summary.json").exists():
            self.skipTest("historical recorded runs are not available")
        before = {p: analysis.sha(p / "summary.json") for p in (e4, e5)}
        result = analysis.matrix_analysis(e4, analysis.load(e4 / "summary.json"))
        self.assertTrue(result["complete"])
        self.assertEqual(result["valid_measurements"], 2)
        self.assertEqual(result["expected_measurements"], 2)
        self.assertEqual(result["covered_configurations"], 8)
        self.assertEqual(result["expected_configurations"], 8)
        self.assertEqual(result["alias_count"], 6)
        self.assertTrue(all(c["equal_weight_mean"]["improvement_pct"] is None for c in result["comparisons"]))
        decision = analysis.decision_analysis(e5, analysis.load(e5 / "summary.json"))
        self.assertEqual(decision["scientific_status"], "no_decision_increment")
        self.assertEqual(decision["new_gpu_restore_count"], 0)
        self.assertEqual(before, {p: analysis.sha(p / "summary.json") for p in (e4, e5)})


if __name__ == "__main__":
    unittest.main()
