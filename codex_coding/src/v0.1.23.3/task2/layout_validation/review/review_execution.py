"""Audit saved four-layout execution locally against independent frozen predictions."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / "codex_coding/results/v0.1.23.3/task2/layout_validation"
PREDICTIONS = RESULTS / "review_preflight_r01/predictions.json"
PREDICTIONS_SHA = "fdf95d99484a3d9fbcee39b16262aefe244b258f9d3180d9a0cae468c3c1b5d6"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(body, frozen):
    checks, details = {}, {}
    def check(name, value):
        checks[name] = bool(value)
    check("completed", body.get("status") == "passed")
    check("one_two16MiB_case", len(body.get("cases", [])) == 1
          and body["cases"][0].get("block_bytes") == 16 << 20)
    if not checks["one_two16MiB_case"]:
        return checks, details
    case = body["cases"][0]
    layouts = case.get("layouts", {})
    check("same_session_ABCD_order", list(layouts) == ["A", "B", "C", "D"])
    check("base_retirement", case.get("cleanup_errors") == []
          and case.get("owned_replicas_remaining") == [])
    check("synthetic_scope", body.get("model_weights_ready_measured") is False
          and body.get("performance_claim_ready") is False and body.get("no_solver_delta") is True)
    all_digests, identities, domains = [], [], []
    for name, predicted in frozen["layouts"].items():
        row = layouts.get(name, {})
        transfer = row.get("transfer", {})
        plan = transfer.get("plan", {})
        ops = plan.get("ops", [])
        check(name+"/passed", row.get("status") == "passed")
        fields = ("tp", "src_w", "dst_w", "n", "kind")
        check(name+"/actual_paths", [{k: o.get(k) for k in fields} for o in ops] == predicted["ops"])
        check(name+"/executor", plan.get("executor") == "serial"
              and plan.get("chunk_bytes") == 32 << 20 and plan.get("prediction_ready") is False
              and not any(k in plan for k in ("estimated_transfer_s", "bandwidth_lower_bound_s", "serial_target_times_s")))
        executions = transfer.get("execution", [])
        check(name+"/two_workers", [r.get("worker") for r in executions] == [0, 1])
        record_details = {}
        for worker in executions:
            wid = worker["worker"]
            records = worker.get("records", [])
            expected = predicted["expected_records_by_worker"][str(wid)]
            actual = [dict(tp=r.get("op_id"), role=r.get("role"), bytes=r.get("bytes"), kind=r.get("kind")) for r in records]
            check(f"{name}/worker{wid}_roles_bytes_order", actual == expected)
            check(f"{name}/worker{wid}_replica_binding", all(r.get("replica_id") == ops[r["op_id"]].get("replica_id") for r in records))
            check(f"{name}/worker{wid}_primitive", worker.get("executor") == "serial"
                  and worker.get("chunk_bytes") == 32 << 20 and worker.get("staging_bytes") == 64 << 20)
            record_details[str(wid)] = records
        inventory = row.get("cache_built_inventory", [])
        catalogs = {r["id"]: r for w in inventory for r in w["replicas"]}
        check(name+"/source_generation_epoch_identity", len(ops) == 2 and all(
            (lambda r: r.get("state") == "VALID" and r.get("leases") == 0
             and all(r.get(k) == o.get(v) for k, v in (("generation", "generation"), ("epoch", "source_epoch"), ("identity", "identity"), ("worker", "src_w"), ("tp", "tp"))))(catalogs.get(o.get("replica_id"), {})) for o in ops))
        identities.append([o.get("identity") for o in ops])
        ledger = row.get("physical_ledger", [])
        check(name+"/common_physical_ledger", len(ledger) == 2 and all(
            r.get("base_dram_bytes") == (32 << 20 if r["worker"] == 0 else 0)
            and r.get("cache_hbm_bytes") == predicted["cache_bytes_by_worker"][r["worker"]]
            and r.get("target_hbm_bytes") == 16 << 20 and r.get("staging_hbm_bytes") == 64 << 20 for r in ledger)
            and row.get("cache_allocated_bytes") == 32 << 20)
        gate = row.get("budget_gate", {})
        check(name+"/common_gate_8MiB", gate.get("passed") is True and len(gate.get("resources", [])) == 2 and all(
            r.get("dynamic_margin_bytes") == 8 << 20 and r.get("additional_application_hbm_bound_bytes") == 48 << 20
            and all(r.get(f"projected_{tier}_increment_bytes") == r.get(f"fixed_runtime_{tier}_increment_bytes", -1)+r.get(f"additional_application_{tier}_bound_bytes", -1)+(8 << 20)
                    and r[f"projected_{tier}_increment_bytes"] <= 2 << 30 for tier in ("dram", "hbm")) for r in gate.get("resources", [])))
        verification = row.get("verification", {})
        verified = verification.get("operations", {})
        check(name+"/full_SHA", verification.get("passed") is True and verification.get("checked_bytes") == 32 << 20
              and set(verified) == {"0", "1"} and all(r.get("n") == 16 << 20 and r.get("source_sha256") == r.get("target_sha256")
              and len(r.get("target_sha256", "")) == 64 for r in verified.values()))
        all_digests.append([verified.get(str(i), {}).get("target_sha256") for i in (0, 1)])
        timing = transfer.get("controller_timing", {})
        domains.append(timing.get("clock_domain"))
        begin, filled, published = [timing.get(k) for k in ("load_begin_ns", "all_targets_filled_ns", "all_targets_published_ns")]
        valid = all(type(v) is int for v in (begin, filled, published)) and begin <= filled <= published
        check(name+"/same_clock_order", valid and row.get("clock_domain") == timing.get("clock_domain"))
        check(name+"/phase_math", valid and abs((filled-begin)/1e9-row.get("block_buffers_filled_s", -1)) < 1e-12
              and abs((published-begin)/1e9-row.get("published_collection_ready_s", -1)) < 1e-12
              and abs((filled-begin)/1e9-(transfer.get("prepare_s", 0)+transfer.get("execute_wall_s", 0))) < 1e-5
              and abs((published-filled)/1e9-transfer.get("verify_and_publish_s", -1)) < 1e-5)
        clean_inventories = [row.get(k, []) for k in ("cache_built_inventory", "after_inventory", "after_cleanup_inventory")]
        check(name+"/leases_transactions_clean", all(len(inv) == 2 and all(not w.get("transactions")
              and not w.get("transaction_phases") and all(r.get("leases") == 0 for r in w.get("replicas", [])) for w in inv) for inv in clean_inventories))
        check(name+"/layout_retirement", row.get("cleanup_errors") == [] and row.get("owned_replicas_remaining") == []
              and len(row.get("after_cleanup_inventory", [])) == 2 and all(w["used_bytes"]["hbm"] == 0 for w in row["after_cleanup_inventory"]))
        details[name] = dict(filled_ms=row.get("block_buffers_filled_s", 0)*1000,
            published_ms=row.get("published_collection_ready_s", 0)*1000,
            prepare_ms=transfer.get("prepare_s", 0)*1000, execute_wall_ms=transfer.get("execute_wall_s", 0)*1000,
            verify_publish_ms=transfer.get("verify_and_publish_s", 0)*1000,
            cache_preparation_ms=row.get("preparation_cost_s", 0)*1000, records=record_details,
            SHA256=all_digests[-1], network_payload_bytes=predicted["cross_node_payload_bytes"])
    check("same_content_identity", len(identities) == 4 and all(i == identities[0] for i in identities))
    check("same_full_SHA", len(all_digests) == 4 and all(i == all_digests[0] for i in all_digests))
    check("same_controller_domain", len(set(domains)) == 1 and bool(domains[0]))
    return checks, details


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if sha(PREDICTIONS) != PREDICTIONS_SHA:
        raise ValueError("independent pre-run predictions changed")
    if args.out.resolve().parent != RESULTS or not args.out.name.startswith("review_"):
        raise ValueError("require a fresh review output")
    args.out.mkdir(parents=True, exist_ok=False)
    checks, details = audit(json.loads(args.execution.read_text()), json.loads(PREDICTIONS.read_text()))
    ready = all(checks.values())
    filled = {k: v["filled_ms"] for k, v in details.items()}
    time_hypotheses = dict(status="unresolved")
    if ready and set(filled) == {"A", "B", "C", "D"}:
        time_hypotheses = dict(filled_order=sorted(filled, key=filled.get),
            A_shortest="supported_this_observation" if filled["A"] == min(filled.values()) else "refuted_this_observation",
            D_longest="supported_this_observation" if filled["D"] == max(filled.values()) else "refuted_this_observation",
            B_vs_C="B_faster" if filled["B"] < filled["C"] else "C_faster" if filled["C"] < filled["B"] else "equal")
    result = dict(schema_version="fluidckpt.layout_validation.execution_review.v1", checks=checks,
        execution_passed=ready, failures=[k for k, v in checks.items() if not v], layouts=details,
        time_hypotheses=time_hypotheses, runtime_path_and_exact_exit="pending_separate_original_artifact_review",
        performance_ready=False, model_weights_ready_measured=False, formal_capacity_profile_admitted=False,
        full_physical_peak_verified=False, solver_decision_increment_proven=False)
    (args.out / "qualification.json").write_text(json.dumps(result, indent=2)+"\n")
    refs = {str(p.resolve()): dict(sha256=sha(p), bytes=p.stat().st_size) for p in (Path(__file__), PREDICTIONS, args.execution)}
    (args.out / "evidence_refs.json").write_text(json.dumps(refs, indent=2)+"\n")
    print(json.dumps(dict(out=str(args.out), execution_passed=ready, failures=result["failures"], time_hypotheses=time_hypotheses)))
    raise SystemExit(0 if ready else 1)


if __name__ == "__main__":
    main()
