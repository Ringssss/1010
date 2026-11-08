"""CPU-only finite sensitivity check using existing planners and service scorer."""
import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.cluster.multi_joint import joint_scenarios
from fluidckpt.cluster.path_catalog import topology_id
from fluidckpt.cluster.planning import path_for, restore_plan
from fluidckpt.cluster.service_cost import estimate_execution, new_state
from fluidckpt.benchmarks.frozen_layout import strong_simple

N = 16 << 20


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def fixture():
    workers = [dict(worker=i, node=f"node{node}", gpu=gpu, numa=numa,
                    capabilities=["local_dram", "local_hbm", "network"],
                    session="symbolic_only", epoch=f"symbolic_epoch_{i}")
               for i, node, gpu, numa in ((0, 28, 0, 0), (1, 30, 4, 4))]
    base = [dict(id=f"base/{tp}", identity="two16MiB_fixture", tp=tp, off=0, n=N,
                 worker=0, node="node28", gpu=0, numa=0, tier="dram",
                 state="VALID", generation=1, leases=0) for tp in (0, 1)]
    candidates = [dict(id=f"cache/{tp}/{w['worker']}", identity="two16MiB_fixture",
                       tp=tp, off=0, n=N, tier="hbm", state="VALID", generation=1,
                       leases=0, **{k: w[k] for k in ("worker", "node", "gpu", "numa")})
                  for tp in (0, 1) for w in workers]
    targets = [dict(key=f"target/{tp}", identity="two16MiB_fixture", tp=tp, off=0,
                    n=N, worker=tp, tier="hbm") for tp in (0, 1)]
    return workers, base, candidates, targets


def profile_model(workers, base, candidates, targets, local_ms, network_ms, dram_ms=40):
    profile = dict(rates=dict(local_dram=1, local_hbm=1, network=1),
                   gpu_ingress_GBps=1000, gpu_egress_GBps=1000,
                   dram_GBps=1000, network_node_GBps=1000,
                   serial_target_execution=True, network_pairs=[[0, 1], [1, 0]],
                   provenance="SYMBOLIC SENSITIVITY: no measured rate/capacity")
    model = dict(schema_version=1, executor="serial", topology_id=topology_id(workers),
                 dispatch_s=0, paths={}, scope="symbolic coefficients; existing worker-order scorer")
    wm = {w["worker"]: w for w in workers}
    for r in base+candidates:
        for t in targets:
            if r["tp"] != t["tp"]:
                continue
            p = path_for(r, t, wm, profile)
            ms = dram_ms if r["tier"] == "dram" else local_ms if r["worker"] == t["worker"] else network_ms
            rate = N / 1e9 / (ms / 1000)
            profile.setdefault("directed_path_rates", {})[p["path"]] = rate
            model["paths"][p["path"]] = dict(warm_GBps=rate, operation_s=0, first_pair_s=0)
    return profile, model


def signature(plan):
    return [{k: o[k] for k in ("replica_id", "src_w", "dst_w", "off", "n", "path")}
            for o in plan["ops"]]


def legal(layout):
    return len(layout)*N <= 2*N and all(sum(c["n"] for c in layout if c["worker"] == w) <= N for w in (0, 1))


def inspect(local_ms, network_ms, dram_ms=40, both_at_28=False):
    workers, base, candidates, targets = fixture()
    if both_at_28:
        targets = [dict(t, worker=0) for t in targets]
    for w in workers:
        w.update(used_bytes=dict(hbm=0, dram=2*N if w['worker'] == 0 else 0),
                 budget_bytes=dict(hbm=N, dram=2*N))
    profile, model = profile_model(workers, base, candidates, targets, local_ms, network_ms, dram_ms)
    def evaluate(layout, routing="fastest"):
        plan = restore_plan(base+layout, targets, workers, profile, routing)
        score = estimate_execution(plan, workers, model, new_state(workers))
        return dict(selected_ids=sorted(c["id"] for c in layout),
                    bandwidth_bound_ms=plan["estimated_transfer_s"]*1000,
                    ordered_service_ms=score["estimated_execute_s"]*1000,
                    ordered_ops=signature(plan), timeline=score["timeline"])
    rows = [evaluate(list(subset)) for count in range(len(candidates)+1)
            for subset in itertools.combinations(candidates, count) if legal(subset)]
    best = min(rows, key=lambda r: (r["ordered_service_ms"], len(r["selected_ids"]), r["selected_ids"]))
    chosen, steps = [], []
    while True:
        before = evaluate(chosen)
        options = [(evaluate(chosen+[c]), c) for c in candidates
                   if c not in chosen and legal(chosen+[c])]
        if not options:
            break
        after, candidate = min(options, key=lambda x: (x[0]["ordered_service_ms"], x[1]["id"]))
        if after["ordered_service_ms"] >= before["ordered_service_ms"]-1e-9:
            break
        chosen.append(candidate)
        steps.append(dict(candidate_id=candidate["id"], before_ms=before["ordered_service_ms"],
                          after_ms=after["ordered_service_ms"]))
    greedy = evaluate(chosen)
    scenarios = [dict(name="fixed_targets", weight=1, targets=targets)]
    case = dict(workers=workers, base=base, candidates=candidates, restore_scenarios=scenarios,
                objective="expected", budget=dict(tier_budgets={"hbm": 2*N},
                location_budgets={"0:hbm": N, "1:hbm": N}, base_dram_bytes=2*N,
                target_allocation_bytes=2*N, staging_bytes=128 << 20))
    simple = strong_simple(case, profile)
    existing_simple = evaluate(simple["selected"], "resource_lp")
    existing_simple["winner"] = simple["winner"]
    joint = joint_scenarios(base, candidates, scenarios, workers, profile,
                            {"hbm": 2*N}, {"0:hbm": N, "1:hbm": N})
    proposal = evaluate(joint["selected"], "resource_lp")
    proposal["solver_objective_ms"] = joint["objective_s"]*1000
    proposal["solver_status"] = joint["status"]
    gap = proposal["ordered_service_ms"]-best["ordered_service_ms"]
    return dict(case="both_targets_at28_cache_cap_insufficient" if both_at_28 else "original_split_targets_dependency_diagnostic",
                coefficients=dict(local_hbm_ms=local_ms, network_hbm_ms=network_ms,
                                  base_dram_ms=dram_ms, provenance="symbolic, not observations"),
                finite_layout_count=len(rows), layouts=rows, finite_oracle=best,
                diagnostic_ordered_marginal_enumerator=greedy, diagnostic_greedy_steps=steps,
                diagnostic_marginal_is_existing_baseline=False,
                existing_strong_simple=existing_simple, existing_bandwidth_joint=proposal,
                joint_ordered_service_regret_ms=gap,
                greedy_matches_finite_oracle=math.isclose(greedy["ordered_service_ms"], best["ordered_service_ms"], abs_tol=1e-9),
                existing_simple_joint_signature_alias=existing_simple["ordered_ops"] == proposal["ordered_ops"],
                existing_simple_joint_ordered_cost_alias=math.isclose(existing_simple["ordered_service_ms"], proposal["ordered_service_ms"], abs_tol=1e-9),
                interpretation="symbolic objective misranking" if gap > 1e-9 else "no joint advantage under shared ordered-service objective")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    workers, base, candidates, targets = fixture()
    contract = dict(schema_version="decision_validation.finite_cpu.v1", development="v0.1.23.3/task2",
                    workers=workers, base=base, candidates=candidates, targets=targets,
                    tier_budget_bytes={"hbm": 2*N}, location_budget_bytes={"0:hbm": N, "1:hbm": N},
                    budget_scope="prescribed application cache caps; not measured physical free capacity",
                    ledger=dict(base_dram_bytes=2*N, max_cache_hbm_bytes=2*N, target_hbm_bytes=2*N,
                                staging_hbm_bytes_per_worker=64 << 20, runtime_bytes="unknown/fresh guard required"),
                    scenario_scope="one fixed restore; no simultaneous restore/scenario claims",
                    candidate_freeze="all four block/location candidates, including duplicate-placement opportunity",
                    fairness="existing strong_simple and joint share original LP objective; diagnostic marginal and finite oracle share existing ordered scorer",
                    symbolic_profiles=[[1, 3], [2, 2], [3, 2]],
                    budget_insufficient_profiles=dict(local_hbm_ms=0.5, dram_remote_hbm_ms=[[1, 3], [2, 2], [3, 1]],
                        targets=[dict(t, worker=0) for t in targets], actual_costs="unknown"),
                    unknowns=["same-primitive calibrated service", "fresh budgets", "physical peaks", "real decision delta"],
                    stop_conditions=["Real calibrated same signatures: stop strategy GPU comparison.",
                                     "No real ordered-service benefit over strong simple: stop candidate expansion.",
                                     "Unknown path/capacity: do not fill with symbolic rates or assert performance.",
                                     "Symbolic-only mismatch: record model boundary; do not launch GPU to chase it."],
                    measured_costs=None, performance_ready=False, gpu_launches=0)
    paths = [Path(__file__)] + [ROOT / "main_lib/fluidckpt/cluster" / name for name in
             ("planning.py", "multi_joint.py", "service_cost.py", "managed.py", "control.py", "worker.py")]
    paths.append(ROOT / "main_lib/fluidckpt/benchmarks/frozen_layout.py")
    contract["source_refs"] = [{"path": str(p.relative_to(ROOT)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]
    contract["contract_sha256"] = digest(contract)
    (args.out / "contract.json").write_text(json.dumps(contract, indent=2)+"\n")
    observations = [inspect(*pair) for pair in contract["symbolic_profiles"]]
    observations += [inspect(0.5, remote, dram, True) for dram, remote in
                     contract["budget_insufficient_profiles"]["dram_remote_hbm_ms"]]
    result = dict(contract_sha256=contract["contract_sha256"], cpu_sensitivity=observations,
                  real_measured_decision_delta="unknown", real_joint_benefit_proven=False,
                  dependency_model_available=True, new_solver_required=False,
                  action="stop GPU strategy comparison until independently measured costs change a real decision")
    (args.out / "inspection.json").write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps({"out": str(args.out), "profiles": len(observations),
                      "symbolic_regrets_ms": [r["joint_ordered_service_regret_ms"] for r in observations]}))


if __name__ == "__main__":
    main()
