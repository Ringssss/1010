"""Evaluate immutable preregistered online predictions without fitting holdouts."""
import argparse
import hashlib
import json
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def file_ref(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def verify_ref(ref):
    require(file_ref(ref["path"]) == ref, "changed evidence: " + ref["path"])


def evaluate(frozen, observations):
    """Use integer nanoseconds for the inclusive, per-observation 10% gate."""
    require(frozen.get("schema") == "online_cost_predictions_v1", "prediction schema")
    payload = frozen["payload"]
    require(digest(payload) == frozen["payload_sha256"], "prediction payload changed")
    verify_ref(payload["protocol_ref"])
    protocol = json.loads(Path(payload["protocol_ref"]["path"]).read_text())
    require(protocol.get("schema") == "online_cost_protocol_v1", "protocol schema")
    require(protocol["max_relative_error"] == 0.10, "protocol must use the user 10% gate")
    require(protocol["verification_position"] == "after_all_weights_ready", "SHA boundary")
    require(protocol["model_weights_ready_required"] is True, "actual model boundary required")
    require(payload.get("frozen_before_validation") is True, "freeze was not preregistered")
    require(payload["freeze_wall_ns"] >= protocol["registered_wall_ns"], "freeze before protocol")
    for ref in payload["calibration_refs"]:
        verify_ref(ref)
    predictions = payload["predictions"]
    scenarios = protocol["scenarios"]
    require(set(predictions) == set(scenarios) and 2 <= len(scenarios) <= 3,
            "exactly the preregistered 2–3 scenarios are required")
    require(set(observations) == set(scenarios), "incomplete or unexpected holdouts")
    fairness = [digest({k: scenario["context"][k] for k in
                       ("weight_identity", "target_signature", "allowed_budgets", "comparison_group", "plan_workload")})
                for scenario in scenarios.values()]
    require(len(set(fairness)) == 1, "layouts must share content, target and allowed budgets")
    train_sessions = set(payload["calibration_sessions"])
    require(train_sessions and all(train_sessions), "missing calibration session IDs")
    independence = protocol["independence_mode"]
    require(independence in ("new_request_current_incarnation", "fresh_session"), "independence mode")
    training_runs = set(payload["calibration_run_ids"])
    require(training_runs and all(training_runs), "calibration run IDs required")
    checks, rows, values = [], {}, {}
    validation_runs = set()
    validation_incarnations = set()
    calibration_incarnations = set(payload["calibration_incarnation_digests"])
    actual_all = True
    for name, specification in scenarios.items():
        prediction = predictions[name]
        estimate = prediction["estimated_online_ns"]
        require(type(estimate) is int and estimate > 0, "positive integer prediction required")
        require(prediction["context_sha256"] == digest(specification["context"]), "prediction context mismatch")
        records = observations[name]
        require(type(records) is list and len(records) == specification["validation_samples"],
                "holdout count must match the preregistered count")
        require(1 <= len(records) <= 2, "one validation normally; at most two preregistered")
        detail = []
        for record in records:
            require(record["scenario"] == name and record["context_sha256"] == prediction["context_sha256"],
                    "holdout identity/layout/clock/budget/configuration changed")
            require(record["session"], "missing holdout session")
            require(record["run_id"] not in training_runs, "holdout reuses calibration request")
            if independence == "fresh_session":
                require(record["session"] not in train_sessions, "holdout shares calibration session")
            require(record["run_id"] not in validation_runs, "duplicate holdout run")
            validation_runs.add(record["run_id"])
            incarnation = record["incarnation_sha256"]
            require(incarnation, "missing current incarnation")
            if independence == "fresh_session":
                require(incarnation not in calibration_incarnations, "holdout shares calibration incarnation")
            else:
                require(incarnation in payload["registered_incarnation_digests"], "holdout uses unregistered incarnation")
            validation_incarnations.add(incarnation)
            require(record["load_begin_wall_ns"] > payload["freeze_wall_ns"], "holdout predates freeze")
            require(record["fresh_binding_verified"] is True, "current incarnation/config/source binding not checked")
            require(record["all_target_weights_usable"] is True, "buffers filled do not certify model readiness")
            require(record["exact_reference_output_passed"] is True, "fixed model reference output failed")
            require(record["verification_position"] == "after_all_weights_ready", "holdout SHA is online")
            require(record["full_sha_passed"] is True and record["verified_bytes"] == specification["logical_bytes"],
                    "full logical-weight SHA failed or incomplete")
            require(record["resource_gate_passed"] is True and record["cleanup_passed"] is True,
                    "execution correctness/resource lifecycle failed")
            if specification["context"].get("requires_shared_overlap"):
                require(record.get("shared_phase_overlap_confirmed") is True, "shared holdout lacks actual phase overlap proof")
            require(record["clock_domain"] == specification["context"]["clock_domain"], "clock domain mismatch")
            begin, ready = record["load_begin_ns"], record["all_weights_ready_ns"]
            require(type(begin) is int and type(ready) is int and 0 <= begin < ready, "invalid monotonic boundaries")
            actual = ready - begin
            error = estimate - actual
            passed = 10 * abs(error) <= actual
            actual_all &= record.get("evidence_kind") == "actual_model_online"
            for ref in record["source_refs"]:
                verify_ref(ref)
            require(record["source_refs"], "missing raw execution evidence")
            row = {"run_id": record["run_id"], "session": record["session"],
                   "estimated_online_ns": estimate, "actual_online_ns": actual,
                   "signed_error_ns": error, "absolute_error_ns": abs(error),
                   "relative_error": error / actual, "error_gate_passed": passed,
                   "source_refs": record["source_refs"]}
            detail.append(row)
            checks.append(passed)
        rows[name] = {"observations": detail, "scenario_gate_passed": all(x["error_gate_passed"] for x in detail)}
        values[name] = [x["actual_online_ns"] for x in detail]
    ranking = []
    names = sorted(scenarios)
    for i, left in enumerate(names):
        for right in names[i+1:]:
            require(scenarios[left]["context"]["comparison_group"] == scenarios[right]["context"]["comparison_group"],
                    "ranking scenarios must share content/target/allowed-budget comparison group")
            delta = predictions[left]["estimated_online_ns"] - predictions[right]["estimated_online_ns"]
            pleft = predictions[left]["estimated_online_ns"]
            pright = predictions[right]["estimated_online_ns"]
            # Use the same 10% error scale for a ranking abstention policy. This
            # is a resolution convention, not a future confidence/error bound.
            claim = 1 if 9*pleft > 11*pright else -1 if 11*pleft < 9*pright else 0
            # Show every observed cross-product; never average away a reversal.
            for a in values[left]:
                for b in values[right]:
                    observed = a - b
                    ranking.append({"left": left, "right": right,
                                    "predicted_delta_ns": delta, "observed_delta_ns": observed,
                                    "ordering_agrees": (delta > 0) - (delta < 0) == (observed > 0) - (observed < 0),
                                    "rank_claim": "left_slower" if claim > 0 else "left_faster" if claim < 0 else "abstain",
                                    "rank_claim_agrees": claim == (observed > 0) - (observed < 0) if claim else None})
    return {"schema": "online_cost_validation_v1", "scenario_results": rows,
            "all_scenario_error_gates_passed": all(checks),
            "actual_model_online_qualified": all(checks) and actual_all,
            "evidence_kind": "actual_model_online" if actual_all else "CPU_or_nonmodel_fixture",
            "ranking": ranking, "all_pairwise_rankings_agree": all(r["ordering_agrees"] for r in ranking),
            "ranking_claims_made": sum(r["rank_claim"] != "abstain" for r in ranking),
            "all_nonabstained_rank_claims_agree": all(r["rank_claim_agrees"] for r in ranking if r["rank_claim"] != "abstain"),
            "rank_abstention_scope": "Overlapping intervals implied by the protocol's 10% scale abstain; this is not a confidence interval or certified future error bound.",
            "prediction_payload_sha256": frozen["payload_sha256"],
            "calibration_sessions": sorted(train_sessions),
            "independence_mode": independence,
            "validation_incarnation_digests": sorted(validation_incarnations),
            "holdouts_used_for_fitting": False,
            "physical_capacity_certified": False,
            "scope": "Only preregistered complete online scenarios; ranking is reported separately from per-scenario accuracy."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen", required=True)
    parser.add_argument("--observations", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    frozen = json.loads(Path(args.frozen).read_text())
    result = evaluate(frozen, json.loads(Path(args.observations).read_text()))
    result["frozen_ref"] = file_ref(args.frozen)
    result["observations_ref"] = file_ref(args.observations)
    with Path(args.output).open("x") as stream:
        stream.write(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({k: result[k] for k in ("all_scenario_error_gates_passed", "actual_model_online_qualified")}))


if __name__ == "__main__":
    main()
