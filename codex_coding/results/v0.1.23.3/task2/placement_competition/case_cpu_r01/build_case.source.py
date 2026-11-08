"""Freeze and evaluate one finite content-location competition on CPU only."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.benchmarks.frozen_layout import evaluate, layout_ledger, strong_simple
from fluidckpt.cluster.multi_joint import joint_scenarios
from fluidckpt.cluster.path_catalog import path_key, topology_id
from fluidckpt.cluster.planning import path_for, validate_plan
from fluidckpt.cluster.service_cost import estimate_execution, new_state

UNIT = 4 << 20
QUANTUM = 4096
TP4_SOURCE = ROOT / "codex_coding/src/v0.1.23.3/task2/tp4_scenarios/contract/build_contract.py"
spec = importlib.util.spec_from_file_location("recorded_tp4", TP4_SOURCE)
tp4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tp4)


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fixture():
    old, _ = tp4.make_case("unused", [2, 2, 0, 0], True)
    workers = old["workers"][:6]
    for w in workers:
        i = w["worker"]
        w.update(node=(f"symbolic_pool_{i}" if i < 2 else f"symbolic_target_{i}" if i < 4 else "symbolic_U"),
                 gpu=i, numa=i, epoch=f"placement_symbolic_{i}", session="offline_placement_competition",
                 used_bytes=dict(hbm=0, dram=2*UNIT if i >= 4 else 0),
                 budget_bytes=dict(hbm=2*UNIT if i < 2 else 0, dram=2*UNIT))
    identity = "synthetic_tp4_one4MiB_block_per_rank_not_model_weights"
    def replica(rank, worker, tier, rid):
        w = workers[worker]
        return dict(id=rid, identity=identity, tp=rank, off=0, n=UNIT, tier=tier,
                    worker=worker, node=w["node"], gpu=w["gpu"], numa=w["numa"], epoch=w["epoch"],
                    state="VALID", generation=1, leases=0, preview_only=tier == "hbm", allocated_bytes=UNIT)
    base = [replica(r, 4+r%2, "dram", f"base/{r}") for r in range(4)]
    candidates = [replica(r, p, "hbm", f"cache/r{r}/pool{p}") for r in range(4) for p in range(2)]
    targets = [dict(key=f"target/{r}", identity=identity, tp=r, off=0, n=UNIT, worker=r, tier="hbm") for r in range(4)]
    case = dict(case_id="scarce_pool_flexible_rank", workers=workers, base=base, candidates=candidates,
                restore_scenarios=[dict(name="all_four_ranks_required", weight=1, targets=targets)], objective="expected",
                budget=dict(tier_budgets={"hbm": 4*UNIT},
                    location_budgets={f"{w}:hbm": 2*UNIT if w < 2 else 0 for w in range(6)},
                    base_dram_bytes=4*UNIT, target_allocation_bytes=4*UNIT, staging_bytes=6*(64 << 20)))
    profile = dict(rates=dict(local_hbm=4*UNIT/1e9, local_dram=4*UNIT/1e9, network=UNIT/1e9),
                   gpu_ingress_GBps=100*UNIT/1e9, gpu_egress_GBps=100*UNIT/1e9,
                   dram_GBps=100*UNIT/1e9, network_node_GBps=4*UNIT/1e9,
                   serial_target_execution=True, network_pairs=[], directed_path_rates={},
                   provenance="SYMBOLIC_ONLY: 4MiB/unit, no hardware calibration")
    wm = {w["worker"]: w for w in workers}
    paths = []
    for replica in base+candidates:
        r, w = replica["tp"], replica["worker"]
        if w != r:
            profile["network_pairs"].append([w, r])
        rate = 1 if replica["tier"] == "dram" else 4 if w == 0 or w == r else 3 if r == 0 else .5
        key = path_key(w, replica["tier"], replica["numa"], r, "hbm")
        profile["directed_path_rates"][key] = rate*UNIT/1e9
    profile["network_pairs"] = sorted(map(list, {tuple(p) for p in profile["network_pairs"]}))
    for replica in base+candidates:
        p = path_for(replica, targets[replica["tp"]], wm, profile)
        assert p is not None
        paths.append(dict(replica_id=replica["id"], source_worker=replica["worker"], target_rank=replica["tp"],
                          target_worker=replica["tp"], path=p["path"], kind=p["kind"],
                          symbolic_rate_units_per_time=p["GBps"]*1e9/UNIT,
                          resources={k: v*1e9/UNIT for k,v in p["resources"].items()},
                          physical_rate_GBps=None, physical_capacity_GBps=None))
    return case, profile, paths


RULES = {
    "base_only": "Full immutable DRAM base only, no extra HBM.",
    "capacity_aware_fastest": "Rank order 0,1,2,3; for each whole block choose fastest candidate with feasible global/location allocation, tie by worker then ID. Build only if strictly faster than the best full DRAM fallback. One copy/content; no global optimization. Reevaluate with common original resource_lp including full DRAM.",
    "existing_strong_simple": "Unmodified frozen_layout.strong_simple singleton/paired and joint_greedy controls; all eight candidates and full DRAM; common resource_lp final score.",
    "bottleneck_balanced": "From empty, choose feasible single addition minimizing (resource score, descending four target additive path service times, allocation bytes), rounded to 10 decimals; accept strict lexicographic improvement; IDs break ties. No oracle.",
    "balanced_exchange": "Start from bottleneck_balanced; inspect removal of 0..2 selected and addition of 0..2 unselected candidates, requiring at least one removal; feasible one-swap or two-exchange neighborhoods. Accept strict same lexicographic key improvement, IDs break ties; repeat until no improvement. No oracle, full-layout enumeration or tunable rounds.",
    "existing_joint": "Unmodified multi_joint.joint_scenarios with full DRAM and same eight candidates/caps; original byte-rounded resource_lp plan; no secondary objective.",
    "finite_reference": "Enumerate all 256 subsets, keep 121 capacity-feasible; fixed original resource_lp/4MiB route rounding/rank_interleaved queue. Reference only, not a baseline or finite execution optimum."
}


def run(case, profile):
    candidates = case["candidates"]
    by_id = {c["id"]: c for c in candidates}
    cache = {}
    def ids(selected):
        return tuple(sorted(c["id"] for c in selected))
    def feasible(selected):
        try:
            layout_ledger(case, selected)
            return True
        except ValueError:
            return False
    def ev(selected):
        key = ids(selected)
        if key not in cache:
            cache[key] = evaluate(case, profile, selected)
        return cache[key]
    def key(selected):
        e = ev(selected)
        p = next(iter(e["plans"].values()))
        ranks = p["serial_target_times_s"]
        return (round(e["objective_s"], 10), tuple(sorted((round(ranks.get(r,0),10) for r in range(4)), reverse=True)),
                layout_ledger(case, selected)["allocated_hbm_bytes"])
    def balanced():
        selected, steps = [], []
        while True:
            options = [(key(selected+[c]), c["id"], selected+[c]) for c in candidates if c["id"] not in ids(selected) and feasible(selected+[c])]
            improving = [row for row in options if row[0] < key(selected)]
            if not improving:
                return selected, steps
            after, added, new = min(improving, key=lambda row: row[:2])
            steps.append(dict(before=key(selected), after=after, added=[added]))
            selected = new
    def exchange(start):
        selected, steps = list(start), []
        while True:
            unseen, options = [c for c in candidates if c["id"] not in ids(selected)], {}
            for count in range(1, min(2,len(selected))+1):
                for removed in itertools.combinations(selected,count):
                    kept = [c for c in selected if c not in removed]
                    for nadd in range(3):
                        for added in itertools.combinations(unseen,nadd):
                            trial = kept+list(added)
                            if feasible(trial) and key(trial) < key(selected):
                                options[ids(trial)] = (key(trial), ids(trial), trial, removed, added)
            if not options:
                return selected, steps
            after, _, new, removed, added = min(options.values(), key=lambda row: row[:2])
            steps.append(dict(before=key(selected), after=after, removed=[c["id"] for c in removed], added=[c["id"] for c in added]))
            selected = new
    def score(selected):
        e = ev(selected)
        plan = next(iter(e["plans"].values()))
        validate_plan(plan)
        assert plan["strategy"] == "resource_lp" and plan["fallback_reason"] is None
        model = dict(schema_version=1, executor="serial", topology_id=topology_id(case["workers"]), dispatch_s=0,
                     paths={op["path"]: dict(warm_GBps=profile["directed_path_rates"][op["path"]], operation_s=0, first_pair_s=0) for op in plan["ops"]},
                     scope="Symbolic isolated path durations and worker-order DAG; shared contention unmodeled")
        service = estimate_execution(plan, case["workers"], model, new_state(case["workers"]))
        last, queue = {}, []
        for index, op in enumerate(plan["ops"]):
            involved = {op["src_w"], op["dst_w"]}
            queue.append(dict(queue_position=index, op=op, depends_on_queue_positions=sorted({last[w] for w in involved if w in last})))
            for w in involved:
                last[w] = index
        return dict(selected_ids=list(ids(selected)), resource_lp_bound_symbolic_time=e["objective_s"],
                    rank_key=key(selected), ledger=layout_ledger(case,selected), plan=plan,
                    executable_queue=queue, worker_dependency_score=service, predicted_real_T_ready_s=None,
                    shared_resource_finite_schedule_verified=False)
    fastest = []
    wm = {w["worker"]:w for w in case["workers"]}
    for rank in range(4):
        target = case["restore_scenarios"][0]["targets"][rank]
        options = [c for c in candidates if c["tp"] == rank and feasible(fastest+[c])]
        if options:
            chosen = min(options, key=lambda c:(-path_for(c,target,wm,profile)["GBps"], c["worker"],c["id"]))
            fallback_rate = max(path_for(c,target,wm,profile)["GBps"] for c in case["base"] if c["tp"] == rank)
            if path_for(chosen,target,wm,profile)["GBps"] > fallback_rate:
                fastest.append(chosen)
    simple = strong_simple(case,profile)
    balanced_ids, balanced_steps = balanced()
    exchanged, exchange_steps = exchange(balanced_ids)
    b = case["budget"]
    joint = joint_scenarios(case["base"], candidates, case["restore_scenarios"], case["workers"],profile,
                            b["tier_budgets"], b["location_budgets"], solver_options={"time_limit":5})
    selections = {"base_only":[], "capacity_aware_fastest":fastest, "existing_strong_simple":simple["selected"],
                  "bottleneck_balanced":balanced_ids,"balanced_exchange":exchanged,"existing_joint":joint["selected"]}
    policies = {name:score(s) for name,s in selections.items()}
    policies["bottleneck_balanced"]["steps"] = balanced_steps
    policies["balanced_exchange"]["steps"] = exchange_steps
    policies["existing_strong_simple"].update(winner=simple["winner"], controls={name:dict(selected_ids=list(ids(row["selected"])),
                       resource_lp_bound_symbolic_time=row["evaluation"]["objective_s"], steps=row.get("steps")) for name,row in simple["controls"].items()})
    policies["existing_joint"].update(solver_status=joint["status"],continuous_solver_bound_symbolic_time=joint["objective_s"],
                                     rounded_plan_symbolic_time=joint["rounded_plan_s"])
    finite = []
    for mask in range(1 << len(candidates)):
        selected = [c for i,c in enumerate(candidates) if mask & (1 << i)]
        if feasible(selected):
            finite.append(score(selected))
    best = min(finite,key=lambda r:(round(r["resource_lp_bound_symbolic_time"],10),r["ledger"]["allocated_hbm_bytes"],r["selected_ids"]))
    pareto = []
    for row in sorted(finite,key=lambda r:(r["ledger"]["allocated_hbm_bytes"],r["resource_lp_bound_symbolic_time"],r["selected_ids"])):
        cost,space = round(row["resource_lp_bound_symbolic_time"],10),row["ledger"]["allocated_hbm_bytes"]
        if any(other["ledger"]["allocated_hbm_bytes"] <= space and round(other["resource_lp_bound_symbolic_time"],10) <= cost and
               (other["ledger"]["allocated_hbm_bytes"] < space or round(other["resource_lp_bound_symbolic_time"],10) < cost) for other in finite):
            continue
        if not any(p["cache_hbm_bytes"] == space and p["resource_lp_bound_symbolic_time"] == cost for p in pareto):
            pareto.append(dict(selected_ids=row["selected_ids"],cache_hbm_bytes=space,resource_lp_bound_symbolic_time=cost))
    for row in policies.values():
        minimum = min(r["ledger"]["allocated_hbm_bytes"] for r in finite if r["resource_lp_bound_symbolic_time"] <= row["resource_lp_bound_symbolic_time"]+1e-8)
        row["cache_bytes_above_finite_minimum_at_no_worse_primary_cost"] = row["ledger"]["allocated_hbm_bytes"]-minimum
    return dict(case_id=case["case_id"],policies=policies,finite_layouts=finite,
                finite_reference=dict(feasible_count=len(finite),best_selected_ids=best["selected_ids"],
                    best_resource_lp_bound_symbolic_time=best["resource_lp_bound_symbolic_time"],
                    minimum_cache_hbm_bytes=best["ledger"]["allocated_hbm_bytes"],scope=RULES["finite_reference"]),
                pareto=pareto,real_joint_advantage_proven=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out",type=Path,required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    case,profile,paths = fixture()
    sources = [Path(__file__),TP4_SOURCE,ROOT/"codex_coding/results/v0.1.23.3/task2/tp4_scenarios/contract_cpu_r03/contract.json"]
    sources += [ROOT/"main_lib/fluidckpt"/p for p in ("benchmarks/frozen_layout.py","benchmarks/topology_cases.py","cluster/planning.py","cluster/multi_joint.py","cluster/service_cost.py","cluster/path_catalog.py")]
    contract = dict(schema_version="placement_competition.symbolic_contract.v1",development="v0.1.23.3/task2",
        created_at_utc=datetime.now(timezone.utc).isoformat(),stage="preregistered_before_first_solve",synthetic=True,
        case=case,symbolic_profile=profile,path_contract=paths,baseline_rules=RULES,
        budget_ledger=dict(block_bytes=UNIT,allocation_quantum_bytes=QUANTUM,routing_quantum_bytes=UNIT,
            base_dram_bytes=4*UNIT,target_hbm_bytes=4*UNIT,max_cache_hbm_bytes=4*UNIT,
            cache_pool_caps_bytes=[2*UNIT,2*UNIT],staging_hbm_bytes=6*(64 << 20),runtime_bytes=None,
            physical_peak_bytes=None,physical_budget_admitted=False,physical_payload_allocated_bytes=0),
        competition_witness=dict(scarce_pool=0,high_quality_dependent_ranks=[0,2,3],demand_units=3,capacity_units=2,
            flexible_rank=0,flexible_alternative_rate=3,dependent_alternative_rates=[.5,.5],
            inequalities=["3 high-quality block demands > 2 pool0 slots","r0 alternative3 > r2/r3 alternative0.5"],
            role_reuse="Target0/1 also cache source pools; cross-role worker dependencies differ from previous eight-worker TP4."),
        decision_contract=dict(full_dram_fallback_for_all=True,duplicate_copies_allowed=True,relay=False,
            schedule="Original rank_interleaved ops with shared source/destination worker ready dependencies",
            primary="Common original rounded resource_lp resource/target-service score, symbolic dimensionless",
            secondary="Descending target additive service vector then cache bytes; finite service DAG separately reported",
            route_choice="All legal base plus chosen caches participate; candidate selection does not force cache use",
            shared_exit="Two DRAM source workers share net_out:symbolic_U at4units/time; not claimed isolated cause of gain"),
        failure_conditions=["Only IDs differ or strong simple equals joint: report no primary joint increment",
            "Fallback makes added caches unused: report unused allocation; do not force HBM use",
            "Shared bound and worker DAG differ: neither certifies finite shared schedule",
            "Physical runtime/source epochs/calibration/model binding absent: retain readiness false",
            "Any failure preserves run; no parameter sweep or changes to obtain joint win"],
        source_refs=[dict(path=str(p.relative_to(ROOT)),sha256=sha(p)) for p in sources],
        readiness=dict(physical_mapping=False,physical_capacity=False,finite_execution=False,model_binding=False,real_performance=False),
        gpu_command=None,gpu_launches=0,ssh_operations=0,product_changes=0)
    contract["contract_sha256"] = canonical(contract)
    write(args.out/"contract.json",contract)
    (args.out/"build_case.source.py").write_bytes(Path(__file__).read_bytes())
    try:
        comparison = run(case,profile)
        comparison["contract_sha256"] = contract["contract_sha256"]
        write(args.out/"comparison.json",comparison)
        checks = dict(eight_candidates=len(case["candidates"]) == 8,
            four_targets=len({t["worker"] for t in case["restore_scenarios"][0]["targets"]}) == 4,
            unique_worker_gpu=len({(w["node"],w["gpu"]) for w in case["workers"]}) == 6,
            each_content_two_pools=all({c["worker"] for c in case["candidates"] if c["tp"] == r} == {0,1} for r in range(4)),
            scarce_pool_demand_exceeds_slots=3 > 2,reference_121_subsets=len(comparison["finite_layouts"]) == 121,
            full_base_accounted=sum(r["n"] for r in case["base"]) == 4*UNIT,
            all_source_sha_unchanged=all(sha(ROOT/r["path"]) == r["sha256"] for r in contract["source_refs"]),
            no_route_fallback=all(r["plan"]["fallback_reason"] is None for r in comparison["finite_layouts"]),
            bounds_not_below_reference=all(r["resource_lp_bound_symbolic_time"]+1e-8 >= comparison["finite_reference"]["best_resource_lp_bound_symbolic_time"] for r in comparison["policies"].values()))
        write(args.out/"checks.json",dict(checks=checks,passed=all(checks.values())))
        assert all(checks.values()),checks
        summary = {name:dict(resource_lp_bound_symbolic_time=row["resource_lp_bound_symbolic_time"],cache_hbm_bytes=row["ledger"]["allocated_hbm_bytes"],selected_ids=row["selected_ids"],worker_dependency_symbolic_time=row["worker_dependency_score"]["estimated_execute_s"]) for name,row in comparison["policies"].items()}
        write(args.out/"summary.json",summary)
        print(json.dumps(dict(status="passed_cpu_symbolic_only",out=str(args.out),summary=summary)))
    except Exception as exc:
        write(args.out/"failure.json",dict(error_type=type(exc).__name__,error=str(exc),contract_preserved=True,gpu_launches=0))
        raise


if __name__ == "__main__":
    main()
