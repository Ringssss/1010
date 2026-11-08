"""Freeze all scenario predictions before any independent online request."""
import argparse
import json
from pathlib import Path
import time

from evaluate import digest, file_ref, require, verify_ref
from predictor import plan_workload, predict


def freeze(protocol_path, model_path, inputs_path):
    protocol = json.loads(Path(protocol_path).read_text())
    model = json.loads(Path(model_path).read_text())
    inputs = json.loads(Path(inputs_path).read_text())
    require(protocol["schema"] == "online_cost_protocol_v1", "protocol schema")
    require(protocol["max_relative_error"] == .10 and protocol["model_weights_ready_required"] is True,
            "full online per-scenario ten percent criterion required")
    require(protocol["verification_position"] == "after_all_weights_ready", "SHA position")
    require(protocol["independence_mode"] in ("new_request_current_incarnation", "fresh_session"), "independence mode")
    require(set(inputs["scenarios"]) == set(protocol["scenarios"]) and 2 <= len(protocol["scenarios"]) <= 3,
            "freeze every preregistered scenario together")
    require(inputs["calibration_run_ids"], "calibration request lineage required")
    require(inputs["calibration_incarnation_digests"], "calibration incarnation lineage required")
    predictions, incarnations = {}, set()
    for name, scenario in inputs["scenarios"].items():
        definition = protocol["scenarios"][name]
        require(definition["context"]["clock_domain"] == model["context"]["clock_domain"], "clock domain changed")
        require(definition["context"]["plan_workload"] == plan_workload(scenario["plan"]), "full-model extent work changed")
        require(definition["logical_bytes"] == sum(t["n"] for t in scenario["plan"]["targets"]), "logical weight size changed")
        result = predict(scenario["plan"], scenario["workers"], model, scenario["state"])
        predictions[name] = dict(result, context_sha256=digest(definition["context"]), plan_sha256=digest(scenario["plan"]))
        incarnations.add(digest(scenario["state"]["incarnations"]))
    refs = [file_ref(model_path), file_ref(inputs_path), *model["source_refs"]]
    refs += [ref for scenario in inputs["scenarios"].values() for ref in scenario["state"]["binding_source_refs"]]
    refs = list({(ref["path"], ref["sha256"]): ref for ref in refs}.values())
    for ref in refs:
        verify_ref(ref)
    timestamp = time.time_ns()
    require(timestamp >= protocol["registered_wall_ns"], "protocol was not registered before freeze")
    payload = dict(protocol_ref=file_ref(protocol_path), frozen_before_validation=True,
                   freeze_wall_ns=timestamp, calibration_refs=refs, calibration_sessions=model["calibration_sessions"],
                   calibration_run_ids=inputs["calibration_run_ids"],
                   calibration_incarnation_digests=inputs["calibration_incarnation_digests"],
                   registered_incarnation_digests=sorted(incarnations), model_sha256=digest(model),
                   predictions=predictions, evidence_scope="Preregistered transfer/handoff hypothesis; independent full-model validation pending.")
    return dict(schema="online_cost_predictions_v1", payload=payload, payload_sha256=digest(payload))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    frozen = freeze(args.protocol, args.model, args.inputs)
    with Path(args.output).open("x") as stream:
        stream.write(json.dumps(frozen, indent=2, sort_keys=True, allow_nan=False)+"\n")
    print(json.dumps({"payload_sha256": frozen["payload_sha256"], "artifact": file_ref(args.output)}))


if __name__ == "__main__":
    main()
