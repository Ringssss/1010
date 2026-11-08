"""Predeclared cost identification and independent holdout analysis (CPU only)."""
import argparse
import hashlib
import json
import math
from fractions import Fraction
from pathlib import Path
from statistics import median

SCHEMA = "cost-ledger-v1"
MODE = "native_serial_thread_scoped_noevents_v1"
CASES = {"L1": [32], "L2": [64], "L3": [32, 32], "L4": [48, 16],
         "L5": [96], "L3_repeat": [32, 32], "V1": [16, 16, 16], "V2": [40, 24, 16]}
CALIBRATION = ["L3", "L1", "L4", "L2", "L5", "L3_repeat"]
HOLDOUT = ["V2", "V1"]
CANDIDATES = {"a_bN": [0, 1], "a_bN_cK": [0, 1, 2], "a_bN_cK_dC": [0, 1, 2, 3]}
LAYERS = ["service_ns", "worker_ns", "controller_execute_ns", "full_filled_ns"]
LIMITS = {"service_ns": 50000, "worker_ns": 250000,
          "controller_execute_ns": 1000000, "full_filled_ns": 2000000}
EXPECTED_STATE = dict(configuration_id="singleworker_node27_gpu0_local_native_v1",
    path="local_hbm", executor="serial", chunk_bytes=32*1048576, source_bytes=96*1048576,
    source_worker=0, target_worker=0, source_tier="hbm", target_tier="hbm", target_mode="replica",
    topology="single_worker_node27_gpu0_world1", inventory_mode="cached", control_mode="compact",
    target_allocation="fresh_exact_size_each_request", target_writes_before=0,
    handler="native_ThreadingHTTPServer_request_thread", queues=0, pretouch=False,
    CUDA_events=0, copy_rounds=1, logger="native_json_stdout", profile_execute=False)


class Rejected(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Rejected(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def encoded(value):
    if isinstance(value, Fraction):
        return {"numerator": value.numerator, "denominator": value.denominator, "value": float(value)}
    if isinstance(value, dict):
        return {key: encoded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [encoded(item) for item in value]
    return value


def number(value):
    if isinstance(value, dict) and "numerator" in value:
        return Fraction(value["numerator"], value["denominator"])
    return Fraction(value)


def chunks(parts):
    return [min(32, n - p) for n in parts for p in range(0, n, 32)]


def row(case):
    parts = CASES[case]
    return [1, sum(parts), len(parts), len(chunks(parts))]


def rank(matrix):
    work = [[Fraction(v) for v in line] for line in matrix]
    pivot = 0
    for col in range(len(work[0])):
        found = next((i for i in range(pivot, len(work)) if work[i][col]), None)
        if found is None:
            continue
        work[pivot], work[found] = work[found], work[pivot]
        scale = work[pivot][col]
        work[pivot] = [v / scale for v in work[pivot]]
        for i in range(len(work)):
            if i != pivot:
                scale = work[i][col]
                work[i] = [v - scale * w for v, w in zip(work[i], work[pivot])]
        pivot += 1
    return pivot


def ols(matrix, values):
    """Solve the exact normal equations; retain signed unconstrained coefficients."""
    require(rank(matrix) == len(matrix[0]), "rank_deficient")
    columns = list(zip(*matrix))
    work = [[sum(Fraction(a) * b for a, b in zip(left, right)) for right in columns]
            + [sum(Fraction(a) * b for a, b in zip(left, values))] for left in columns]
    for col in range(len(columns)):
        pivot = next(i for i in range(col, len(work)) if work[i][col])
        work[col], work[pivot] = work[pivot], work[col]
        scale = work[col][col]
        work[col] = [v / scale for v in work[col]]
        for i in range(len(work)):
            if i != col:
                scale = work[i][col]
                work[i] = [v - scale * w for v, w in zip(work[i], work[col])]
    return [line[-1] for line in work]


def protocol():
    return {
        "protocol_version": "cost-identification-predeclared-v1", "schema_version": SCHEMA,
        "measurement_mode": MODE, "calibration_order": CALIBRATION, "fit_samples": CALIBRATION[:5],
        "expected_state_contract": EXPECTED_STATE,
        "expected_configuration": dict(EXPECTED_STATE, measurement_mode=MODE),
        "drift_only_samples": ["L3_repeat"], "holdout_order": HOLDOUT, "max_requests": 8,
        "cases": {key: {"operation_mib": parts, "chunk_mib": chunks(parts), "row": row(key)}
                  for key, parts in CASES.items()},
        "primary": "complete_native_execute_method_outer_wall_ns", "diagnostic": "sum_op_service_ns",
        "candidates": CANDIDATES, "fit": "unconstrained_exact_OLS_no_clamping_first_five_only",
        "coefficient_units": ["ns", "ns_per_logical_MiB", "ns_per_op", "ns_per_chunk"],
        "coefficient_meaning": "conditional_effective_cost_not_physical_bandwidth",
        "scale": {"fixed_floor_ns": 5000, "clock": "clock_extra_p95_ns",
                  "logger": "dummy_log_scale_ns", "drift": "abs(L3_repeat-L3)", "formula": "max"},
        "drift_gate": {"bound_ns": 20000, "anchor_mean_fraction": 0.1,
                       "required_layers": ["worker_ns", "service_ns"]},
        "structure": {"c": "L3-L2 > 2E and positive", "d": "L4-L3 > 2E and positive",
                      "32b": "L2-L1-d > 4E and positive", "fifth": "abs(L5-2L2+L1)<=4E",
                      "candidate_max_training_residual": "<=2E",
                      "simple_candidates": "own residual and raw nonnegative coefficients suffice"},
        "nonnegative_validity": "All included raw OLS coefficients, including intercept, must be >=0; this is a predeclared modeling choice, never a clamp",
        "outer": "first-five medians(controller_execute-worker,full_filled-controller_execute), each once",
        "independent_limits_ns": LIMITS, "independent_relative_limit": 0.2,
        "independent_rule": "absolute AND relative for every point and layer; never grow absolute limits with E",
        "selection_before_holdout": "simplest admissible worker candidate; service independently simplest admissible",
        "selection_after_holdout": "retain the preregistered simplest if qualified; any finer qualified candidate requires >the corresponding layer E improvement in absolute error versus preregistered simplest at every holdout point/layer, even if preregistered simplest fails; otherwise complexity_increment_unproven",
        "failure": "any worker or service drift failure or no admissible worker candidate skips holdout explicitly",
        "domain": {"path": "local_hbm", "executor": "native", "scheduler": "serial", "chunk_mib": 32,
                   "targets": "fresh_same_allocation_policy_nonoverlap", "events": False,
                   "source_bootstrap_mib_per_session": 96},
        "rejection": ["schema", "mode", "configuration", "state_contract", "stale_session",
                      "incomplete_or_unverified", "counts", "source_hash_change", "missing_raw_evidence"],
        "published_prediction": None, "live_prediction": None,
        "training_goodness": "qualification_only_not_independent_validation",
        "ledger": {"exact_ns_partitions": True, "float_compatibility_tolerance_ns": 1000,
                   "raw_audit_required": True, "flags_alone_do_not_authorize_GPU": True}}


def verify_predeclared(predeclared):
    require(predeclared["protocol"] == protocol(), "protocol_mismatch")
    require(predeclared["protocol_sha256"] == canonical(protocol()), "protocol_hash_mismatch")
    for ref in predeclared["source_refs"]:
        require(digest(ref["path"]) == ref["sha256"], "source_hash_change:" + ref["path"])


def validate_bundle(bundle, sequence):
    require(bundle.get("schema_version") == SCHEMA, "schema_mismatch")
    require(bundle.get("measurement_mode") == MODE, "mode_mismatch")
    require(isinstance(bundle.get("configuration"), dict) and bundle["configuration"], "configuration_missing")
    require(isinstance(bundle.get("state_contract"), dict) and bundle["state_contract"], "state_contract_missing")
    require(bundle["configuration"] == dict(EXPECTED_STATE, measurement_mode=MODE), "configuration_mismatch")
    require(bundle["state_contract"] == EXPECTED_STATE, "state_contract_mismatch")
    require(isinstance(bundle.get("session_id"), str) and bundle["session_id"], "session_missing")
    require(bundle.get("qualification", {}).get("passed") is True, "incomplete_final_lifecycle")
    require(bundle.get("cleanup_ref"), "missing_final_cleanup_evidence")
    require(digest(bundle["cleanup_ref"]["path"]) == bundle["cleanup_ref"]["sha256"], "cleanup_evidence_hash_mismatch")
    require([s.get("case_id") for s in bundle.get("samples", [])] == sequence, "incomplete_or_order_mismatch")
    request_ids, target_ids = set(), set()
    for sample in bundle["samples"]:
        case = sample["case_id"]
        require(sample.get("session_id") == bundle["session_id"], "stale_session")
        require(sample.get("measurement_mode", MODE) == MODE, "sample_mode_mismatch")
        require(sample.get("request_id") and sample["request_id"] not in request_ids, "request_identity_reuse")
        require(sample.get("target_id") and sample["target_id"] not in target_ids, "target_identity_reuse")
        request_ids.add(sample["request_id"])
        target_ids.add(sample["target_id"])
        require(all(sample.get("flags", {}).get(key) is True for key in
                    ["completed", "full_sha_passed", "ledger_closed", "lifecycle_passed"]), "incomplete_or_unverified")
        expected = row(case)
        actual = sample.get("actual", {})
        require([actual.get("N_mib"), actual.get("K"), len(actual.get("chunk_mib", []))] == expected[1:], "NKC_mismatch")
        require(actual["chunk_mib"] == chunks(CASES[case]), "chunk_vector_mismatch")
        require(actual.get("copy_count") == expected[3] == actual.get("sync_count"), "copy_sync_mismatch")
        require(actual.get("logical_bytes") == expected[1] * 1048576 == actual.get("submitted_copy_bytes"), "byte_mismatch")
        for layer in LAYERS:
            value = sample.get("measured", {}).get(layer)
            require(type(value) is int and value > 0, "missing_or_invalid_measurement:" + layer)
        require(sample["measured"]["service_ns"] <= sample["measured"]["worker_ns"] <=
                sample["measured"]["controller_execute_ns"] <= sample["measured"]["full_filled_ns"], "layer_order")
        require(sample.get("raw_refs"), "missing_raw_evidence")
        for ref in sample["raw_refs"]:
            require(digest(ref["path"]) == ref["sha256"], "raw_evidence_hash_mismatch")
    observer = bundle.get("observer", {})
    for key in ["clock_extra_p95_ns", "dummy_log_scale_ns"]:
        require(type(observer.get(key)) in [int, float] and math.isfinite(observer[key]) and observer[key] >= 0,
                "observer_missing_or_invalid")


def analyze_layer(samples, observer, layer):
    values = {s["case_id"]: Fraction(s["measured"][layer]) for s in samples}
    drift = abs(values["L3_repeat"] - values["L3"])
    scale = max(Fraction(5000), Fraction(str(observer["clock_extra_p95_ns"])),
                Fraction(str(observer["dummy_log_scale_ns"])), drift)
    drift_limit = max(Fraction(20000), (values["L3_repeat"] + values["L3"]) / 20)
    c = values["L3"] - values["L2"]
    d = values["L4"] - values["L3"]
    b32 = values["L2"] - values["L1"] - d
    fifth = values["L5"] - 2 * values["L2"] + values["L1"]
    structural = {"c_signal": c > 2 * scale, "d_signal": d > 2 * scale,
                  "b32_signal": b32 > 4 * scale, "fifth_consistency": abs(fifth) <= 4 * scale}
    candidates = {}
    for name, columns in CANDIDATES.items():
        matrix = [[row(case)[i] for i in columns] for case in CALIBRATION[:5]]
        coefficients = ols(matrix, [values[case] for case in CALIBRATION[:5]])
        fitted = [sum(a * b for a, b in zip(line, coefficients)) for line in matrix]
        residuals = {case: values[case] - value for case, value in zip(CALIBRATION[:5], fitted)}
        nonnegative = all(v >= 0 for v in coefficients)
        residual_pass = max(abs(v) for v in residuals.values()) <= 2 * scale
        structure_pass = name != "a_bN_cK_dC" or all(structural.values())
        admissible = nonnegative and residual_pass and structure_pass
        candidates[name] = {"columns": columns, "raw_coefficients": coefficients,
                            "coefficient_status": ["conditional_aggregate" if admissible and (i == 0 or
                                                   (name != "a_bN_cK_dC" and (i != 1 or structural["b32_signal"]))) else
                                                   "identified_conditional" if admissible and
                                                   (structural["b32_signal"] if i == 1 else structural["c_signal"] if i == 2 else structural["d_signal"])
                                                   else "unresolved" for i in columns],
                            "rank": rank(matrix), "training_residual_ns": residuals,
                            "training_max_abs_residual_ns": max(abs(v) for v in residuals.values()),
                            "nonnegative_pass": nonnegative, "residual_pass": residual_pass,
                            "structure_pass": structure_pass, "admissible": admissible,
                            "training_is_validation": False,
                            "predictions": {case: sum(Fraction(row(case)[i]) * a for i, a in zip(columns, coefficients))
                                            for case in HOLDOUT}}
    return {"raw_cost_ns": values, "E_ns": scale, "drift_ns": drift, "drift_limit_ns": drift_limit,
            "drift_pass": drift <= drift_limit,
            "differences_ns": {"c_L3_minus_L2": c, "d_L4_minus_L3": d,
                               "b32_L2_minus_L1_minus_d": b32, "fifth_L5_minus_2L2_plus_L1": fifth},
            "structural_gates": structural, "candidates": candidates,
            "selected_before_holdout": next((name for name, item in candidates.items() if item["admissible"]), None)}


def calibrate(bundle, predeclared, bundle_ref=None):
    verify_predeclared(predeclared)
    validate_bundle(bundle, CALIBRATION)
    layers = {layer: analyze_layer(bundle["samples"], bundle["observer"], layer)
              for layer in ["worker_ns", "service_ns"]}
    first5 = bundle["samples"][:5]
    controller_residuals = [s["measured"]["controller_execute_ns"] - s["measured"]["worker_ns"] for s in first5]
    filled_residuals = [s["measured"]["full_filled_ns"] - s["measured"]["controller_execute_ns"] for s in first5]
    controller = Fraction(median(controller_residuals))
    filled = Fraction(median(filled_residuals))
    observed_scales = {layer: max(Fraction(5000), Fraction(str(bundle["observer"]["clock_extra_p95_ns"])),
                                 Fraction(str(bundle["observer"]["dummy_log_scale_ns"])),
                                 abs(Fraction(bundle["samples"][-1]["measured"][layer]) -
                                     bundle["samples"][0]["measured"][layer])) for layer in LAYERS}
    admitted = all(item["drift_pass"] for item in layers.values()) and layers["worker_ns"]["selected_before_holdout"] is not None
    all_predictions = {}
    for name, candidate in layers["worker_ns"]["candidates"].items():
        all_predictions[name] = {case: {"worker_ns": candidate["predictions"][case],
                                       "controller_execute_ns": candidate["predictions"][case] + controller,
                                       "full_filled_ns": candidate["predictions"][case] + controller + filled,
                                       "published_ns": None, "live_ns": None}
                                 for case in HOLDOUT}
    return {"artifact_type": "cost-calibration-frozen-v1", "protocol": protocol(),
            "CPU_REPLAY_ONLY": bundle.get("CPU_REPLAY_ONLY", False),
            "protocol_sha256": canonical(protocol()), "predeclared": predeclared,
            "calibration_bundle_ref": bundle_ref, "calibration_session_id": bundle["session_id"],
            "configuration": bundle["configuration"], "state_contract": bundle["state_contract"],
            "configuration_sha256": canonical(bundle["configuration"]), "state_sha256": canonical(bundle["state_contract"]),
            "request_ids": [s["request_id"] for s in bundle["samples"]],
            "target_ids": [s["target_id"] for s in bundle["samples"]], "layers": layers,
            "observed_layer_scales_ns": observed_scales,
            "outer": {"method": "median_first_five_only_no_additional_fit",
                      "controller_minus_worker_raw_ns": controller_residuals,
                      "filled_minus_controller_raw_ns": filled_residuals,
                      "controller_residual_ns": controller, "filled_residual_ns": filled},
            "layered_raw_breakdown": [{"case_id": s["case_id"], "measured": s["measured"],
                                      "worker_minus_service_ns": s["measured"]["worker_ns"] - s["measured"]["service_ns"],
                                      "controller_minus_worker_ns": s["measured"]["controller_execute_ns"] - s["measured"]["worker_ns"],
                                      "filled_minus_controller_ns": s["measured"]["full_filled_ns"] - s["measured"]["controller_execute_ns"]}
                                     for s in bundle["samples"]],
            "diagnostic_candidate_predictions": all_predictions,
            "predictions": [{"case_id": case, "worker_and_outer": all_predictions[layers["worker_ns"]["selected_before_holdout"]][case],
                             "service_ns": layers["service_ns"]["candidates"][layers["service_ns"]["selected_before_holdout"]]["predictions"][case]
                             if layers["service_ns"]["selected_before_holdout"] else None} for case in HOLDOUT] if admitted else [],
            "selected_worker_before_holdout": layers["worker_ns"]["selected_before_holdout"] if admitted else None,
            "frozen_scope": "admitted_C1_candidates" if admitted else "diagnostic_C1",
            "product_admission": False, "holdout_authorized_by_analysis": admitted,
            "holdout_status": "conditional_new_session_pending" if admitted else "skipped_predeclared_C1_gate_failed",
            "frozen": True, "hardware_admission": False, "independently_validated": False,
            "published_prediction": None, "live_prediction": None}


def error(prediction, actual, layer):
    signed = number(prediction) - actual
    absolute = abs(signed)
    relative = absolute / actual
    return {"predicted_ns": number(prediction), "actual_ns": actual, "signed_error_ns": signed,
            "absolute_error_ns": absolute, "relative_error": relative,
            "absolute_pass": absolute <= LIMITS[layer], "relative_pass": relative <= Fraction(1, 5),
            "passed": absolute <= LIMITS[layer] and relative <= Fraction(1, 5)}


def select_independent(candidates, scales, relevant_layers):
    baseline = next((name for name, item in candidates.items() if item["admissible"]), None)
    if baseline is None:
        return None
    selected = baseline if candidates[baseline]["all_points_pass"] else None
    for name, item in candidates.items():
        if not item["admissible"] or not item["all_points_pass"]:
            continue
        if name == baseline:
            continue
        proven = all(candidates[baseline]["errors"][case][layer]["absolute_error_ns"] -
                     item["errors"][case][layer]["absolute_error_ns"] > scales[layer]
                     for case in HOLDOUT for layer in relevant_layers)
        item["complexity_increment_proven"] = proven
        item["retention_status"] = "complexity_increment_proven" if proven else "complexity_increment_unproven"
        if proven and (selected is None or all(candidates[selected]["errors"][case][layer]["absolute_error_ns"] -
                                             item["errors"][case][layer]["absolute_error_ns"] > scales[layer]
                                             for case in HOLDOUT for layer in relevant_layers)):
            selected = name
    return selected


def validate_holdout(bundle, frozen):
    require(frozen.get("artifact_type") == "cost-calibration-frozen-v1" and frozen.get("frozen") is True,
            "not_frozen")
    verify_predeclared(frozen["predeclared"])
    require(frozen["protocol_sha256"] == canonical(protocol()), "frozen_protocol_mismatch")
    require(frozen["holdout_authorized_by_analysis"] is True, "holdout_skipped_C1_gate_failed")
    require(frozen.get("calibration_bundle_ref"), "missing_calibration_bundle_ref")
    ref = frozen["calibration_bundle_ref"]
    require(digest(ref["path"]) == ref["sha256"], "calibration_bundle_changed")
    # Reconstruct the freeze from calibration only, before reading any holdout values.
    expected = encoded(calibrate(load(ref["path"]), frozen["predeclared"], ref))
    require(encoded(frozen) == expected, "frozen_model_or_prediction_tampered")
    validate_bundle(bundle, HOLDOUT)
    require(bundle["session_id"] != frozen["calibration_session_id"], "stale_session")
    require(bundle.get("CPU_REPLAY_ONLY", False) == frozen.get("CPU_REPLAY_ONLY", False), "replay_hardware_mismatch")
    require(canonical(bundle["configuration"]) == frozen["configuration_sha256"], "configuration_mismatch")
    require(canonical(bundle["state_contract"]) == frozen["state_sha256"], "state_contract_mismatch")
    require(not set(frozen["request_ids"]) & {s["request_id"] for s in bundle["samples"]}, "stale_request")
    require(not set(frozen["target_ids"]) & {s["target_id"] for s in bundle["samples"]}, "stale_target")
    worker, service = {}, {}
    by_case = {s["case_id"]: s for s in bundle["samples"]}
    for name, values in frozen["diagnostic_candidate_predictions"].items():
        errors = {case: {layer: error(values[case][layer], by_case[case]["measured"][layer], layer)
                         for layer in LAYERS[1:]} for case in HOLDOUT}
        worker[name] = {"admissible": frozen["layers"]["worker_ns"]["candidates"][name]["admissible"],
                        "errors": errors, "layer_pass": {layer: all(errors[c][layer]["passed"] for c in HOLDOUT)
                                                           for layer in LAYERS[1:]},
                        "all_points_pass": all(e["passed"] for v in errors.values() for e in v.values())}
    for name, item in frozen["layers"]["service_ns"]["candidates"].items():
        errors = {case: {"service_ns": error(item["predictions"][case], by_case[case]["measured"]["service_ns"], "service_ns")}
                  for case in HOLDOUT}
        service[name] = {"admissible": item["admissible"], "errors": errors,
                         "all_points_pass": all(errors[c]["service_ns"]["passed"] for c in HOLDOUT)}
    scales = {layer: number(value) for layer, value in frozen["observed_layer_scales_ns"].items()}
    selected_worker = select_independent(worker, scales, LAYERS[1:])
    selected_service = select_independent(service, scales, ["service_ns"])
    local = [name for name, item in worker.items() if item["admissible"] and item["layer_pass"]["worker_ns"]]
    return {"artifact_type": "cost-independent-validation-v1", "holdout_session_id": bundle["session_id"],
            "CPU_REPLAY_ONLY": bundle.get("CPU_REPLAY_ONLY", False),
            "worker_candidates": worker, "service_candidates": service,
            "selected_worker_and_outer": selected_worker, "selected_service": selected_service,
            "qualified_local_worker_candidates": local,
            "complete_filled_model_qualified": selected_worker is not None,
            "decision_ready": False, "future_error_bound": False,
            "no_holdout_backfill": True, "published_prediction": None, "live_prediction": None,
            "status": "independent_qualification_observed" if selected_worker else "independent_domain_failed",
            "raw_holdout_measured": {case: by_case[case]["measured"] for case in HOLDOUT}}


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("protocol")
    pre.add_argument("--instrument-source", action="append", default=[])
    for name in ["calibrate", "validate"]:
        cmd = sub.add_parser(name)
        cmd.add_argument("--bundle", required=True)
        cmd.add_argument("--predeclared" if name == "calibrate" else "--frozen", required=True)
    for cmd in [pre, *[p for n, p in sub.choices.items() if n != "protocol"]]:
        cmd.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    out = Path(args.output_dir)
    require(not out.exists(), "output_directory_exists_no_clobber")
    if args.command == "protocol":
        paths = sorted(set([str(p.resolve()) for p in Path(__file__).parent.glob("*.py")] +
                           [str(Path(p).resolve()) for p in args.instrument_source]))
        result = {"artifact_type": "cost-predeclared-v1", "protocol": protocol(),
                  "protocol_sha256": canonical(protocol()),
                  "source_refs": [{"path": p, "sha256": digest(p)} for p in paths],
                  "created_before_calibration": True, "hardware_measurements": False}
        filename = "predeclared_protocol.json"
    elif args.command == "calibrate":
        ref = {"path": str(Path(args.bundle).resolve()), "sha256": digest(args.bundle)}
        result = calibrate(load(args.bundle), load(args.predeclared), ref)
        filename = "frozen_prediction.json"
    else:
        result = validate_holdout(load(args.bundle), load(args.frozen))
        result["frozen_ref"] = {"path": str(Path(args.frozen).resolve()), "sha256": digest(args.frozen)}
        filename = "independent_validation.json"
    write_new(out / filename, encoded(result))
    print(json.dumps({"artifact": str(out / filename), "status": result.get("status", result.get("holdout_status", "predeclared"))}))


if __name__ == "__main__":
    main()
