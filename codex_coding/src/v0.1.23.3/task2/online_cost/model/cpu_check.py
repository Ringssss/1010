"""Negative protocol and scheduling checks; all durations are CPU fixtures."""
import argparse
import copy
import json
from pathlib import Path

from evaluate import digest, evaluate, file_ref
from freeze import freeze
from predictor import GIB, build_model, calibrate_outer, new_state, physical, predict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    raw = out / "CPU_FIXTURE_raw.json"
    raw.write_text(json.dumps({"CPU_fixture": True, "GPU_samples": 0}))
    checks = []

    def check(name, value):
        checks.append({"name": name, "passed": bool(value)})
        assert value, name

    def refuses(name, fn):
        try:
            fn()
        except (ValueError, KeyError):
            check(name, True)
        else:
            check(name, False)

    workers = [dict(worker=i, node="target" if i < 2 else "source"+str(i),
                    gpu=i, numa=i, session="CPU_holdout", epoch="CPU_epoch"+str(i)) for i in range(4)]
    contexts = {"near0": dict(endpoints=[0, 0], operation_kind="local_dram", source_tier="dram", source_numa=0),
                "near1": dict(endpoints=[1, 1], operation_kind="local_dram", source_tier="dram", source_numa=1),
                "remote0": dict(endpoints=[2, 0], operation_kind="network", source_tier="dram", source_numa=2),
                "remote1": dict(endpoints=[3, 1], operation_kind="network", source_tier="dram", source_numa=3)}
    context = dict(executor="serial", chunk_bytes=32<<20, control_mode="native", load_state="warm",
                   clock_domain="CPU_controller", clock_scope="single_controller",
                   worker_BDF={str(i): "CPU_BDF"+str(i) for i in range(4)}, worker_physical=physical(workers), path_context=contexts,
                   shared_resource_paths={"target_HCA": ["remote0", "remote1"]}, operation_count_interval=[2, 128],
                   full_model_plan_workload={"targets": [dict(worker=i, identity="CPU_weights", tp=i, off=0, n=GIB) for i in range(2)],
                   "ordered_extents": [dict(dst_w=i, identity="CPU_weights", tp=i, off=0, n=GIB) for i in range(2)],
                   "N": 2*GIB, "K": 2, "C": 64})
    config = digest(context)
    samples = {path: [dict(nbytes=n, service_s=t, evidence_kind="actual_path_calibration", passed=True,
                          verified_bytes=n, config_sha256=config, session="CPU_train", CPU_fixture=True)
                     for n, t in [(GIB//2, .010), (GIB, .018)]] for path in contexts}
    shared = {"target_HCA": [dict(total_bytes=2*GIB, service_s=.050, flow_count=2,
                                evidence_kind="actual_shared_calibration", passed=True,
                                overlap_confirmed=True, resource_binding_confirmed=True,
                                paths=["remote0", "remote1"], config_sha256=config, session="CPU_train", CPU_fixture=True)]}
    shared["target_HCA"][0]["path_bytes"] = {"remote0": GIB, "remote1": GIB}
    model = build_model(context, workers, samples, shared, [file_ref(raw)])
    lineage = {path: dict(worker=row["endpoints"][0], tier=row["source_tier"], numa=row["source_numa"],
                         identity="CPU_weights", generation=1, source_epoch=workers[row["endpoints"][0]]["epoch"])
               for path, row in contexts.items()}
    state = dict(new_state(workers), fresh_binding_verified=True, load_state="warm", config_sha256=config,
                 warm_pairs=["0:2", "1:3"], source_lineage=lineage, worker_BDF=context["worker_BDF"], binding_source_refs=[file_ref(raw)])

    def plan(paths):
        targets = [dict(key="t"+str(i), worker=i, identity="CPU_weights", tp=i, off=0, n=GIB) for i in range(2)]
        ops = [dict(op_id=i, target="t"+str(i), off=0, n=GIB, source_offset=0,
                    src_w=contexts[p]["endpoints"][0], dst_w=i, path=p, replica_id=p,
                    identity="CPU_weights", tp=i, generation=1, source_epoch=lineage[p]["source_epoch"],
                    kind=contexts[p]["operation_kind"]) for i, p in enumerate(paths)]
        return dict(targets=targets, ops=ops, executor="serial", chunk_bytes=32<<20)

    near, remote = plan(["near0", "near1"]), plan(["remote0", "remote1"])
    calibration = dict(evidence_kind="actual_model_online_calibration", full_sha_passed=True,
                       all_target_weights_usable=True, config_sha256=config, plan=remote, workers=workers,
                       state=state, all_weights_ready_ns=70_000_000, load_begin_ns=0,
                       source_ref=file_ref(raw), session="CPU_train", CPU_fixture=True, clock_domain="CPU_controller", clock_scope="single_controller")
    calibration["shared_phase_overlap_confirmed"] = True
    calibration["exact_reference_output_passed"] = True
    calibration.update(verification_position="after_all_weights_ready", verified_bytes=2*GIB, resource_gate_passed=True, cleanup_passed=True)
    bound = calibrate_outer(model, calibration)
    result = predict(remote, workers, bound, state)
    check("isolated disjoint worker DAG overlaps", abs(result["isolated_worker_dag_s"]-.018) < 1e-12)
    check("shared paired factor alters DAG rather than adding a whole floor", abs(result["estimated_transfer_service_s"]-.050) < 1e-12)
    check("outer residual added exactly once", result["estimated_online_ns"] == 70_000_000)
    check("near layout is transferable without whole-layout fit", predict(near, workers, bound, state)["estimated_online_ns"] == 38_000_000)
    bad = copy.deepcopy(remote); bad["ops"][0]["n"] = GIB//4; bad["targets"][0]["n"] = GIB//4
    refuses("reject subcalibration operation bytes", lambda: predict(bad, workers, bound, state))
    refuses("reject unconfirmed warm pair", lambda: predict(remote, workers, bound, dict(state, warm_pairs=[])))
    stale = copy.deepcopy(workers); stale[0]["epoch"] = "other"
    refuses("reject old incarnation state", lambda: predict(remote, stale, bound, state))
    missing = copy.deepcopy(bound); missing["shared_qualifiers"] = {}
    refuses("reject isolated-only shared prediction", lambda: predict(remote, workers, missing, state))
    empty = dict(calibration, evidence_kind="empty_live_sentinel")
    refuses("reject empty readiness calibration", lambda: calibrate_outer(model, empty))
    refuses("reject calibration clock mismatch", lambda: calibrate_outer(model, dict(calibration, clock_domain="other_host")))
    common = dict(weight_identity="CPU_weights", target_signature="CPU_targets", allowed_budgets={"hbm": 2*GIB},
                  comparison_group="CPU_same_content_target_budget", clock_domain="CPU_controller", plan_workload=context["full_model_plan_workload"])
    protocol = dict(schema="online_cost_protocol_v1", max_relative_error=.10,
                    verification_position="after_all_weights_ready", model_weights_ready_required=True,
                    independence_mode="fresh_session",
                    registered_wall_ns=100, scenarios={name: dict(context=dict(common, layout=name), logical_bytes=2*GIB,
                    validation_samples=1) for name in ["near", "remote"]})
    protocol_path = out / "CPU_FIXTURE_protocol.json"
    protocol_path.write_text(json.dumps(protocol, indent=2))
    payload = dict(protocol_ref=file_ref(protocol_path), frozen_before_validation=True, freeze_wall_ns=200,
                   calibration_refs=[file_ref(raw)], calibration_sessions=["CPU_train"],
                   calibration_run_ids=["CPU_train_run"],
                   calibration_incarnation_digests=["CPU_train_epochs"], predictions={name:
                   dict(estimated_online_ns=110 if name=="near" else 100, context_sha256=digest(s["context"]))
                   for name, s in protocol["scenarios"].items()})
    frozen = dict(schema="online_cost_predictions_v1", payload=payload, payload_sha256=digest(payload))
    observations = {name: [dict(scenario=name, context_sha256=digest(s["context"]), session="CPU_holdout",
                   run_id="CPU_run_"+name, incarnation_sha256="CPU_holdout_epochs", load_begin_wall_ns=300,
                   fresh_binding_verified=True, all_target_weights_usable=True,
                   exact_reference_output_passed=True,
                   verification_position="after_all_weights_ready", full_sha_passed=True, verified_bytes=2*GIB,
                   resource_gate_passed=True, cleanup_passed=True, clock_domain="CPU_controller",
                   load_begin_ns=0, all_weights_ready_ns=100, source_refs=[file_ref(raw)], evidence_kind="CPU_fixture")]
                   for name, s in protocol["scenarios"].items()}
    report = evaluate(frozen, observations)
    check("exact ten percent inclusive boundary passes", report["all_scenario_error_gates_passed"])
    check("CPU fixtures never qualify actual model", not report["actual_model_online_qualified"])
    bad = copy.deepcopy(observations); bad["near"][0]["all_weights_ready_ns"] = 99
    check("per-scenario failure cannot average away", not evaluate(frozen, bad)["all_scenario_error_gates_passed"])
    bad = copy.deepcopy(observations); bad["near"][0]["session"] = "CPU_train"
    refuses("reject training session backfill", lambda: evaluate(frozen, bad))
    bad = copy.deepcopy(observations); bad["near"][0]["clock_domain"] = "other_host"
    refuses("reject different clock domain", lambda: evaluate(frozen, bad))
    bad = copy.deepcopy(observations); bad["near"][0]["verification_position"] = "before_ready"
    refuses("reject SHA inside online clock", lambda: evaluate(frozen, bad))
    bad = copy.deepcopy(frozen); bad["payload"]["predictions"]["near"]["estimated_online_ns"] = 100
    refuses("reject prediction mutation after freeze", lambda: evaluate(bad, observations))
    bad = copy.deepcopy(observations); bad["near"][0]["incarnation_sha256"] = "CPU_train_epochs"
    refuses("reject calibration incarnation reuse", lambda: evaluate(frozen, bad))
    check("ranking reversals reported independently", not report["all_pairwise_rankings_agree"])
    check("near-tie ranking abstains", report["ranking_claims_made"] == 0)
    current_protocol = dict(protocol, independence_mode="new_request_current_incarnation")
    current_path = out / "CPU_FIXTURE_current_protocol.json"
    current_path.write_text(json.dumps(current_protocol, indent=2))
    model_path, inputs_path = out / "CPU_FIXTURE_model.json", out / "CPU_FIXTURE_inputs.json"
    model_path.write_text(json.dumps(bound, indent=2))
    inputs = dict(calibration_run_ids=["CPU_train_run"], calibration_incarnation_digests=[digest(state["incarnations"])],
                  scenarios={"near": dict(plan=near, workers=workers, state=state),
                             "remote": dict(plan=remote, workers=workers, state=state)})
    inputs_path.write_text(json.dumps(inputs, indent=2))
    actual_freeze = freeze(current_path, model_path, inputs_path)
    current_obs = copy.deepcopy(observations)
    for name, records in current_obs.items():
        record = records[0]
        record["session"] = "CPU_train"
        record["incarnation_sha256"] = digest(state["incarnations"])
        record["load_begin_wall_ns"] = actual_freeze["payload"]["freeze_wall_ns"]+1
        record["all_weights_ready_ns"] = actual_freeze["payload"]["predictions"][name]["estimated_online_ns"]
    current_result = evaluate(actual_freeze, current_obs)
    check("new requests may reuse explicitly registered incarnation", current_result["all_scenario_error_gates_passed"])
    check("same-incarnation CPU freeze does not certify real model", not current_result["actual_model_online_qualified"])
    bad = copy.deepcopy(current_obs); bad["near"][0]["run_id"] = "CPU_train_run"
    refuses("same incarnation still rejects calibration request reuse", lambda: evaluate(actual_freeze, bad))
    bad = copy.deepcopy(current_obs); bad["near"][0]["exact_reference_output_passed"] = False
    refuses("wrong output cannot qualify despite weights usable", lambda: evaluate(actual_freeze, bad))
    refuses("wrong output cannot calibrate outer despite full SHA", lambda: calibrate_outer(model, dict(calibration, exact_reference_output_passed=False)))
    output = dict(status="CPU_passed", checks=checks, check_count=len(checks), new_GPU_samples=0,
                  actual_model_online_qualified=False, model_fixture_prediction=result, evaluation_fixture=report)
    (out / "qualification.json").write_text(json.dumps(output, indent=2, sort_keys=True)+"\n")
    print(json.dumps({"status": output["status"], "checks": len(checks), "GPU_samples": 0}))


if __name__ == "__main__":
    main()
