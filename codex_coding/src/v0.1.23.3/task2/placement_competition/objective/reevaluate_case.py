"""Independently re-score frozen proposals with the original worker DAG scorer."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.benchmarks.frozen_layout import evaluate, layout_ledger
from fluidckpt.cluster.path_catalog import topology_id
from fluidckpt.cluster.planning import path_for
from fluidckpt.cluster.service_cost import estimate_execution, new_state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.input = args.input.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    contract = json.loads((args.input / "contract.json").read_text())
    comparison = json.loads((args.input / "comparison.json").read_text())
    case, profile = contract["case"], contract["symbolic_profile"]
    known = {c["id"]: c for c in case["candidates"]}
    checks = {}

    def score(row, label, strategy="resource_lp"):
        selected = [known[i] for i in row["selected_ids"]]
        evaluated = evaluate(case, profile, selected, strategy)
        plan = next(iter(evaluated["plans"].values()))
        model = dict(schema_version=1, executor="serial", topology_id=topology_id(case["workers"]),
                     dispatch_s=0., paths={o["path"]: dict(warm_GBps=profile["directed_path_rates"][o["path"]],
                         operation_s=0., first_pair_s=0.) for o in plan["ops"]},
                     scope="Independent original scorer re-evaluation; isolated symbolic rates; shared dynamics unknown")
        service = estimate_execution(plan, case["workers"], model, new_state(case["workers"]))
        ledger = layout_ledger(case, selected)
        target_times = {t["key"]: max((x["end_s"] for o, x in zip(plan["ops"], service["timeline"])
                       if o["target"] == t["key"]), default=0.) for t in plan["targets"]}
        replicas = {r["id"]: r for r in case["base"] + selected}
        targets = {t["key"]: t for t in plan["targets"]}
        wm = {w["worker"]: w for w in case["workers"]}
        paths = [path_for(replicas[o["replica_id"]], targets[o["target"]], wm, profile) for o in plan["ops"]]
        caps = {resource: cap for path in paths for resource, cap in path["resources"].items()}
        endpoints = sorted({t[k] for t in service["timeline"] for k in ("start_s", "end_s")})
        peak_rates = {resource: 0. for resource in caps}
        for begin, end in zip(endpoints, endpoints[1:]):
            middle = (begin + end) / 2
            active = [(o, t, path) for o, t, path in zip(plan["ops"], service["timeline"], paths)
                      if t["start_s"] < middle < t["end_s"]]
            for resource in caps:
                peak_rates[resource] = max(peak_rates[resource], sum(o["n"] / 1e9 / t["service_s"]
                    for o, t, path in active if resource in path["resources"]))
        capacity_feasible = all(peak_rates[r] <= caps[r] + 1e-12 for r in caps)
        checks[label + "/timeline_resource_capacity_feasible"] = capacity_feasible
        if "plan" in row:
            checks[label + "/same_ops"] = row["plan"]["ops"] == plan["ops"]
        if "worker_dependency_score" in row:
            checks[label + "/same_service_score"] = abs(row["worker_dependency_score"]["estimated_execute_s"] - service["estimated_execute_s"]) < 1e-9
        if "resource_lp_bound_symbolic_time" in row:
            checks[label + "/same_resource_score"] = abs(row["resource_lp_bound_symbolic_time"] - evaluated["objective_s"]) < 1e-9
        return dict(selected_ids=row["selected_ids"], worker_dag_symbolic_time=service["estimated_execute_s"],
                    resource_score_symbolic_time=evaluated["objective_s"],
                    allocated_hbm_bytes=ledger["allocated_hbm_bytes"], target_completion_times=target_times,
                    service_order_key=[round(service["estimated_execute_s"], 10),
                       sorted((round(v, 10) for v in target_times.values()), reverse=True),
                       ledger["allocated_hbm_bytes"]], service=service,
                    resource_capacity_feasible=capacity_feasible,
                    routing_strategy=strategy,
                    actual_source_ids=[o["replica_id"] for o in plan["ops"]],
                    resource_peak_GBps=peak_rates, resource_capacity_GBps=caps,
                    feasible_witness_scope="Uniform-rate operations over original zero-setup DAG timeline; all declared path incidence capacities checked, no hardware calibration")

    policies = {name: score(row, "policy/" + name) for name, row in comparison["policies"].items()}
    controls = {name: score(row, "control/" + name)
                for name, row in comparison["policies"]["existing_strong_simple"]["controls"].items()}
    finite = [score(row, "subset/" + str(i)) for i, row in enumerate(comparison["finite_layouts"])]
    route_witnesses = {name: score(row, "witness/" + name, "fastest")
                       for name, row in comparison.get("route_witness", {}).items()}
    actual_used = {op["replica_id"] for row in comparison["finite_layouts"] for op in row["plan"]["ops"]}
    candidate_activity = {rid: rid in actual_used for rid in known}
    route_policy_comparisons = {}
    if route_witnesses:
        checks["all_eight_candidates_used_by_some_common_route"] = all(candidate_activity.values())
        for name, row in route_witnesses.items():
            checks["witness/" + name + "/all_selected_cache_sources_used"] = set(row["selected_ids"]) == set(row["actual_source_ids"])
            expected = 11 / 12 if name == "sequential_full_cache" else 5 / 6
            checks["witness/" + name + "/preregistered_formula"] = abs(row["worker_dag_symbolic_time"] - expected) < 1e-9
            common = next(r for r in finite if sorted(r["selected_ids"]) == sorted(row["selected_ids"]))
            route_policy_comparisons[name] = dict(selected_ids=row["selected_ids"],
                common_resource_lp_source_ids=common["actual_source_ids"],
                fastest_witness_source_ids=row["actual_source_ids"],
                common_resource_lp_worker_dag_time=common["worker_dag_symbolic_time"],
                fastest_witness_worker_dag_time=row["worker_dag_symbolic_time"],
                common_minus_fastest_time=common["worker_dag_symbolic_time"] - row["worker_dag_symbolic_time"],
                same_actual_sources=common["actual_source_ids"] == row["actual_source_ids"],
                scope="Same selected layout, two separately frozen route policies; difference is routing or rounding, not a placement change")
    best = min(finite, key=lambda r: (round(r["worker_dag_symbolic_time"], 10), r["allocated_hbm_bytes"], r["selected_ids"]))
    budgets = sorted({r["allocated_hbm_bytes"] for r in finite})
    budget_best = []
    for budget in budgets:
        row = min((r for r in finite if r["allocated_hbm_bytes"] <= budget),
                  key=lambda r: (round(r["worker_dag_symbolic_time"], 10), r["allocated_hbm_bytes"], r["selected_ids"]))
        budget_best.append(dict(allowed_cache_bytes=budget, selected_ids=row["selected_ids"],
            worker_dag_symbolic_time=row["worker_dag_symbolic_time"], actual_cache_bytes=row["allocated_hbm_bytes"]))
    result = dict(checks=checks, policies=policies, strong_control_proposals=controls,
                  finite_feasible_count=len(finite), finite_worker_dag_best=best,
                  finite_worker_dag_budget_curve=budget_best,
                  candidate_activity_by_common_route=candidate_activity,
                  preregistered_fastest_route_witnesses=route_witnesses,
                  same_layout_route_policy_comparisons=route_policy_comparisons,
                  primary_definition=contract["decision_contract"],
                  scope="Fixed original resource_lp routing and rank_interleaved policy, symbolic isolated worker DAG; not actual shared-resource finite execution optimum",
                  source_refs=[dict(path=str(p.relative_to(ROOT)), sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                      for p in [Path(__file__), args.input / "contract.json", args.input / "comparison.json",
                          ROOT / "main_lib/fluidckpt/cluster/service_cost.py",
                          ROOT / "main_lib/fluidckpt/benchmarks/frozen_layout.py"]],
                  new_GPU_samples=0, SSH_actions=0, product_mutations=0)
    (args.out / "reevaluation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if not all(checks.values()):
        raise SystemExit("independent common scoring mismatch")
    print(json.dumps(dict(passed=len(checks), finite_layouts=len(finite), out=str(args.out))))


if __name__ == "__main__":
    main()
