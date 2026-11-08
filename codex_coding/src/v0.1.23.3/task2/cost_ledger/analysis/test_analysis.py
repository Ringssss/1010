"""CPU protocol checks; synthetic times are never hardware evidence."""
import argparse
import copy
import json
from pathlib import Path
import tempfile
import unittest

import analyze as a


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        raw = self.root / "raw.json"
        raw.write_text('{"synthetic": true}\n')
        self.raw = {"path": str(raw), "sha256": a.digest(raw)}
        cleanup = self.root / "cleanup.json"
        cleanup.write_text('{"synthetic_cleanup": true}\n')
        self.cleanup = {"path": str(cleanup), "sha256": a.digest(cleanup)}
        self.pre = {"protocol": a.protocol(), "protocol_sha256": a.canonical(a.protocol()),
                    "source_refs": [{"path": a.__file__, "sha256": a.digest(a.__file__)}]}

    def tearDown(self):
        self.temp.cleanup()

    def bundle(self, sequence=a.CALIBRATION, session="cal", coefficients=(100000, 1000, 30000, 60000)):
        samples = []
        for index, case in enumerate(sequence):
            vector = a.row(case)
            service = sum(x * y for x, y in zip(vector, coefficients))
            samples.append({"case_id": case, "session_id": session,
                            "request_id": session + "-request-" + str(index),
                            "target_id": session + "-target-" + str(index),
                            "flags": {key: True for key in ["completed", "full_sha_passed", "ledger_closed", "lifecycle_passed"]},
                            "raw_refs": [self.raw],
                            "actual": {"N_mib": vector[1], "K": vector[2], "chunk_mib": a.chunks(a.CASES[case]),
                                       "copy_count": vector[3], "sync_count": vector[3],
                                       "logical_bytes": vector[1] * 1048576, "submitted_copy_bytes": vector[1] * 1048576},
                            "measured": {"service_ns": service, "worker_ns": service + 10000,
                                         "controller_execute_ns": service + 110000, "full_filled_ns": service + 310000,
                                         "published_ns": service + 500000}})
        return {"schema_version": a.SCHEMA, "measurement_mode": a.MODE, "session_id": session,
                "configuration": dict(a.EXPECTED_STATE, measurement_mode=a.MODE),
                "state_contract": dict(a.EXPECTED_STATE),
                "CPU_REPLAY_ONLY": True, "qualification": {"passed": True}, "cleanup_ref": self.cleanup,
                "observer": {"clock_extra_p95_ns": 1000, "dummy_log_scale_ns": 2000}, "samples": samples}

    def freeze(self, bundle=None):
        bundle = bundle or self.bundle()
        path = self.root / "calibration.json"
        a.write_new(path, bundle)
        return a.encoded(a.calibrate(bundle, self.pre, {"path": str(path), "sha256": a.digest(path)}))

    def test_rank_and_exact_ols(self):
        matrix = [a.row(case) for case in a.CALIBRATION[:5]]
        self.assertEqual(a.rank(matrix), 4)
        self.assertEqual(a.ols(matrix, [sum(x * y for x, y in zip(line, [100, 2, 10, 3])) for line in matrix]), [100, 2, 10, 3])
        with self.assertRaisesRegex(a.Rejected, "rank_deficient"):
            a.ols([[1, 1, 1], [1, 2, 2]], [10, 20])

    def test_full_structure_and_predictions(self):
        frozen = self.freeze()
        layer = frozen["layers"]["worker_ns"]
        self.assertTrue(all(layer["structural_gates"].values()))
        self.assertEqual(layer["selected_before_holdout"], "a_bN_cK_dC")
        self.assertEqual([a.number(v) for v in layer["candidates"]["a_bN_cK_dC"]["raw_coefficients"]], [110000, 1000, 30000, 60000])
        self.assertEqual(a.number(frozen["outer"]["controller_residual_ns"]), 100000)
        self.assertEqual(a.number(frozen["outer"]["filled_residual_ns"]), 200000)
        self.assertTrue(frozen["holdout_authorized_by_analysis"])
        result = a.validate_holdout(self.bundle(a.HOLDOUT, "new"), frozen)
        self.assertTrue(result["complete_filled_model_qualified"])
        self.assertIsNone(result["published_prediction"])
        self.assertIsNone(result["live_prediction"])

    def test_anchor_drift_not_used_in_fit(self):
        bundle = self.bundle()
        for key in a.LAYERS:
            bundle["samples"][-1]["measured"][key] += 1000
        frozen = self.freeze(bundle)
        coefficients = frozen["layers"]["worker_ns"]["candidates"]["a_bN_cK_dC"]["raw_coefficients"]
        self.assertEqual([a.number(v) for v in coefficients], [110000, 1000, 30000, 60000])
        self.assertEqual(a.number(frozen["outer"]["controller_residual_ns"]), 100000)

    def test_worker_drift_failure_skips_holdout(self):
        bundle = self.bundle()
        for key in a.LAYERS:
            bundle["samples"][-1]["measured"][key] += 100000
        frozen = self.freeze(bundle)
        self.assertFalse(frozen["holdout_authorized_by_analysis"])
        self.assertEqual(frozen["holdout_status"], "skipped_predeclared_C1_gate_failed")
        with self.assertRaisesRegex(a.Rejected, "holdout_skipped"):
            a.validate_holdout(self.bundle(a.HOLDOUT, "new"), frozen)

    def test_service_drift_alone_skips(self):
        bundle = self.bundle()
        bundle["samples"][-1]["measured"]["service_ns"] -= 50000
        frozen = self.freeze(bundle)
        self.assertTrue(frozen["layers"]["worker_ns"]["drift_pass"])
        self.assertFalse(frozen["layers"]["service_ns"]["drift_pass"])
        self.assertFalse(frozen["holdout_authorized_by_analysis"])

    def test_simple_aggregate_with_unresolved_terms(self):
        frozen = self.freeze(self.bundle(coefficients=(100000, 1000, 0, 0)))
        worker = frozen["layers"]["worker_ns"]
        self.assertEqual(worker["selected_before_holdout"], "a_bN")
        self.assertFalse(worker["candidates"]["a_bN_cK_dC"]["structure_pass"])
        self.assertTrue(frozen["holdout_authorized_by_analysis"])

    def test_weak_size_signal_remains_unresolved(self):
        frozen = self.freeze(self.bundle(coefficients=(100000, 1, 0, 0)))
        candidate = frozen["layers"]["worker_ns"]["candidates"]["a_bN"]
        self.assertTrue(candidate["admissible"])
        self.assertEqual(candidate["coefficient_status"][1], "unresolved")

    def test_negative_coefficient_preserved_not_clamped(self):
        frozen = self.freeze(self.bundle(coefficients=(300000, -1000, 0, 0)))
        candidate = frozen["layers"]["worker_ns"]["candidates"]["a_bN"]
        self.assertEqual(a.number(candidate["raw_coefficients"][1]), -1000)
        self.assertFalse(candidate["nonnegative_pass"])
        self.assertFalse(frozen["holdout_authorized_by_analysis"])

    def test_fifth_difference_and_residual_fail(self):
        bundle = self.bundle()
        for key in a.LAYERS:
            bundle["samples"][4]["measured"][key] += 100000
        frozen = self.freeze(bundle)
        worker = frozen["layers"]["worker_ns"]
        self.assertFalse(worker["structural_gates"]["fifth_consistency"])
        self.assertFalse(worker["candidates"]["a_bN_cK_dC"]["residual_pass"])
        self.assertFalse(frozen["holdout_authorized_by_analysis"])

    def test_medians_not_linear_control_fits(self):
        bundle = self.bundle()
        offsets = [1000, 2000, 3000, 4000, 1000000]
        for sample, offset in zip(bundle["samples"], offsets):
            sample["measured"]["controller_execute_ns"] = sample["measured"]["worker_ns"] + offset
            sample["measured"]["full_filled_ns"] = sample["measured"]["controller_execute_ns"] + 2 * offset
        frozen = self.freeze(bundle)
        self.assertEqual(a.number(frozen["outer"]["controller_residual_ns"]), 3000)
        self.assertEqual(a.number(frozen["outer"]["filled_residual_ns"]), 6000)

    def test_incomplete_count_flags_raw_schema_mode_rejected(self):
        mutations = [
            (lambda b: b["samples"].pop(), "incomplete"),
            (lambda b: b["samples"][0]["flags"].update(full_sha_passed=False), "incomplete"),
            (lambda b: b["samples"][0]["actual"].update(sync_count=1), "copy_sync"),
            (lambda b: b["samples"][0]["actual"].update(chunk_mib=[16, 48]), "chunk_vector"),
            (lambda b: b["samples"][0].update(raw_refs=[]), "missing_raw"),
            (lambda b: b.update(schema_version="old"), "schema"),
            (lambda b: b.update(measurement_mode="events"), "mode"),
            (lambda b: b["samples"][0].update(session_id="old"), "stale_session"),
            (lambda b: b.update(qualification={"passed": False}), "incomplete_final_lifecycle"),
            (lambda b: b.pop("cleanup_ref"), "missing_final_cleanup"),
        ]
        for mutate, reason in mutations:
            with self.subTest(reason=reason):
                bundle = self.bundle()
                mutate(bundle)
                with self.assertRaisesRegex(a.Rejected, reason):
                    a.calibrate(bundle, self.pre)

    def test_holdout_identity_domain_rejection(self):
        frozen = self.freeze()
        mutations = [
            (lambda b: b.update(session_id="cal", samples=self.bundle(a.HOLDOUT, "cal")["samples"]), "stale_session"),
            (lambda b: b["configuration"].update(configuration_id="old-sixworker"), "configuration_mismatch"),
            (lambda b: b["state_contract"].update(targets="rewritten"), "state_contract_mismatch"),
            (lambda b: b["samples"][0].update(request_id="cal-request-0"), "stale_request"),
            (lambda b: b["samples"][0].update(target_id="cal-target-0"), "stale_target"),
            (lambda b: b.update(CPU_REPLAY_ONLY=False), "replay_hardware_mismatch"),
        ]
        for mutate, reason in mutations:
            with self.subTest(reason=reason):
                bundle = self.bundle(a.HOLDOUT, "new")
                mutate(bundle)
                with self.assertRaisesRegex(a.Rejected, reason):
                    a.validate_holdout(bundle, frozen)

    def test_prediction_tampering_rejected(self):
        frozen = self.freeze()
        frozen["diagnostic_candidate_predictions"]["a_bN_cK_dC"]["V2"]["worker_ns"]["numerator"] += 1
        with self.assertRaisesRegex(a.Rejected, "tampered"):
            a.validate_holdout(self.bundle(a.HOLDOUT, "new"), frozen)

    def test_raw_hash_tampering_rejected(self):
        bundle = self.bundle()
        Path(self.raw["path"]).write_text("changed")
        with self.assertRaisesRegex(a.Rejected, "raw_evidence_hash"):
            a.calibrate(bundle, self.pre)

    def test_calibration_tampering_rejected(self):
        frozen = self.freeze()
        Path(frozen["calibration_bundle_ref"]["path"]).write_text("{}")
        with self.assertRaisesRegex(a.Rejected, "calibration_bundle_changed"):
            a.validate_holdout(self.bundle(a.HOLDOUT, "new"), frozen)

    def test_independent_fixed_absolute_AND_relative(self):
        for layer, limit in a.LIMITS.items():
            self.assertFalse(a.error(20000, 10000, layer)["passed"])
            self.assertFalse(a.error(100000000 + limit + 1, 100000000, layer)["passed"])
            self.assertTrue(a.error(100000000 + limit, 100000000, layer)["passed"])

    def test_outer_failure_keeps_worker_qualification(self):
        frozen = self.freeze()
        bundle = self.bundle(a.HOLDOUT, "new")
        for sample in bundle["samples"]:
            sample["measured"]["controller_execute_ns"] += 2000000
            sample["measured"]["full_filled_ns"] += 4000000
        result = a.validate_holdout(bundle, frozen)
        self.assertFalse(result["complete_filled_model_qualified"])
        self.assertIn("a_bN_cK_dC", result["qualified_local_worker_candidates"])

    def test_complexity_uses_each_layer_scale(self):
        def candidate(errors):
            return {"admissible": True, "all_points_pass": True,
                    "errors": {case: {layer: {"absolute_error_ns": v} for layer, v in errors.items()} for case in a.HOLDOUT}}
        values = {"simple": candidate({"worker_ns": 30000, "controller_execute_ns": 30000, "full_filled_ns": 30000}),
                  "fine": candidate({"worker_ns": 10000, "controller_execute_ns": 10000, "full_filled_ns": 10000})}
        self.assertEqual(a.select_independent(values, {"worker_ns": 5000, "controller_execute_ns": 25000, "full_filled_ns": 5000}, a.LAYERS[1:]), "simple")
        self.assertEqual(a.select_independent(values, {layer: 5000 for layer in a.LAYERS}, a.LAYERS[1:]), "fine")

    def test_no_clobber(self):
        path = self.root / "output.json"
        a.write_new(path, {"original": True})
        with self.assertRaises(FileExistsError):
            a.write_new(path, {"original": False})
        self.assertTrue(a.load(path)["original"])

    def test_failed_simple_does_not_automatically_promote_finer(self):
        values = {}
        for name, amount, passed in [("simple", 30000, False), ("fine", 29000, True)]:
            values[name] = {"admissible": True, "all_points_pass": passed,
                            "errors": {case: {layer: {"absolute_error_ns": amount} for layer in a.LAYERS[1:]}
                                       for case in a.HOLDOUT}}
        self.assertIsNone(a.select_independent(values, {layer: 5000 for layer in a.LAYERS}, a.LAYERS[1:]))
        self.assertEqual(values["fine"]["retention_status"], "complexity_increment_unproven")
        for errors in values["fine"]["errors"].values():
            for item in errors.values():
                item["absolute_error_ns"] = 10000
        self.assertEqual(a.select_independent(values, {layer: 5000 for layer in a.LAYERS}, a.LAYERS[1:]), "fine")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output = Path(args.output_dir)
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    with (output / "unittest.log").open("x") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ProtocolTests))
    report = {"status": "passed" if result.wasSuccessful() else "failed", "tests_run": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors),
              "hardware_measurements": False, "synthetic_only": True,
              "source_refs": [{"path": str(p.resolve()), "sha256": a.digest(p)} for p in Path(__file__).parent.glob("*.py")]}
    a.write_new(output / "cpu_qualification.json", report)
    print(json.dumps(report))
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
