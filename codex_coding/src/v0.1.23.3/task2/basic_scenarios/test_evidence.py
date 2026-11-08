"""CPU-only regression and rejection checks for the historical audit."""

import copy
import unittest

from audit_evidence import ROOT, PAPER, RUNS, audit_case, build_summary, historical_manifest, rank_phases, read_json


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = historical_manifest(ROOT)
        cls.summary, cls.records = build_summary(ROOT)
        cls.run_dir = ROOT / PAPER / RUNS["e3"]
        cls.remote_case = read_json(cls.run_dir / "summary.json")["cases"][1]

    def test_original_results_unchanged(self):
        self.assertEqual(self.before, historical_manifest(ROOT))

    def test_references_and_aliases_do_not_create_samples(self):
        self.assertEqual(self.summary["new_samples"], 0)
        self.assertEqual(self.summary["historical_independent_observations"],
                         {"e1": 2, "e3": 4, "e4": 2, "e5": 0, "e6": 8})
        self.assertEqual(self.summary["historical_aliases"], {"e4": 6, "e6": 4})
        self.assertTrue(all(s["new_samples"] == 0 and s["evidence_class"] == "historical_reference"
                            for s in self.summary["scenarios"]))

    def test_real_byte_boundary_and_same_remote_flow(self):
        cases = {(c["label"], c["scenario"]): c for c in self.summary["e3_cases"]}
        self.assertEqual(cases["concentrated", "target_a"]["tier_bytes"]["hbm"], 4 << 30)
        self.assertEqual(cases["distributed", "target_a"]["tier_bytes"]["hbm"], 8 << 30)
        for layout in ("concentrated", "distributed"):
            self.assertEqual(cases[layout, "target_b"]["tier_bytes"],
                             {"hbm": 8 << 30, "dram": 70329718112 - (8 << 30)})
        self.assertAlmostEqual(self.summary["e3_difference"]["saved_percent"], 23.917190333, places=6)

    def test_shared_source_dependency_has_raw_records(self):
        case = self.summary["e3_cases"][1]
        self.assertTrue(case["cross_destination_dependencies"])
        self.assertTrue(any(0 in edge["shared_workers"] for edge in case["cross_destination_dependencies"]))
        self.assertTrue(case["worker_records_match_ordered_plan"])

    def test_cross_host_clock_subtraction_rejected(self):
        with self.assertRaisesRegex(ValueError, "cross-host"):
            rank_phases([{"event": "load_begin", "host": "a", "monotonic_ns": 1},
                         {"event": "weights_ready", "host": "b", "monotonic_ns": 5}])

    def test_deleted_operation_rejected(self):
        case = copy.deepcopy(self.remote_case)
        case["flow"]["execution"][0]["records"].pop()
        with self.assertRaisesRegex(ValueError, "missing or reordered"):
            audit_case(case, self.run_dir)

    def test_wrong_role_rejected(self):
        case = copy.deepcopy(self.remote_case)
        case["flow"]["execution"][0]["records"][0]["role"] = "both"
        with self.assertRaisesRegex(ValueError, "disagrees"):
            audit_case(case, self.run_dir)

    def test_raw_rank_summary_mismatch_rejected(self):
        case = copy.deepcopy(self.remote_case)
        case["timing"]["load_s"] += 0.01
        with self.assertRaisesRegex(ValueError, "rank timing"):
            audit_case(case, self.run_dir)


if __name__ == "__main__":
    unittest.main(verbosity=2)
