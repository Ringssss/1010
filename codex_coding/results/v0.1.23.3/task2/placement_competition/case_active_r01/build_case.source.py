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
from fluidckpt.benchmarks.topology_cases import interleave
from fluidckpt.cluster.multi_joint import joint_scenarios
from fluidckpt.cluster.path_catalog import path_key, topology_id
from fluidckpt.cluster.planning import path_for, restore_plan, validate_plan
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
        rate = 1 if replica["tier"] == "dram" else 4 if w == 0 or w == r else 3 if r == 0 else 1.5
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
    "capacity_aware_fastest": "Rank order 0,1,2,3; for each whole block choose fastest candidate with feasible global/location allocation, tie by path distance (local first), worker then ID. Build only if strictly faster than the best full DRAM fallback. One copy/content; no global optimization. Reevaluate with common original resource_lp including full DRAM.",
    "existing_strong_simple": "Unmodified frozen_layout.strong_simple singleton/paired and joint_greedy controls; all eight candidates and full DRAM; common resource_lp final score.",
    "bottleneck_balanced": "From empty, choose feasible single addition minimizing (original service_cost worker-order DAG all-target completion, descending four target last receiver finish times, allocation bytes), rounded to 10 decimals; accept strict lexicographic improvement; IDs break ties. Common original resource_lp routing; no oracle.",
    "balanced_exchange": "Start from bottleneck_balanced; inspect removal of 0..2 selected and addition of 0..2 unselected candidates, requiring at least one removal; feasible one-swap or two-exchange neighborhoods. Accept strict same lexicographic key improvement, IDs break ties; repeat until no improvement. No oracle, full-layout enumeration or tunable rounds.",
    "existing_joint": "Unmodified multi_joint.joint_scenarios with full DRAM and same eight candidates/caps; original byte-rounded resource_lp plan; no secondary objective.",
    "finite_reference": "Enumerate all 256 subsets, keep 121 capacity-feasible; fixed original resource_lp/4MiB route rounding/rank_interleaved queue. Reference only, not a baseline or finite execution optimum."
}


def run(case, profile):
    candidates = case["candidates"]
    by_id = {c["id"]: c for c in candidates}
    cache = {}
    service_cache = {}
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
    def service(selected):
        signature = ids(selected)
        if signature not in service_cache:
            plan = next(iter(ev(selected)["plans"].values()))
            model = dict(schema_version=1, executor="serial", topology_id=topology_id(case["workers"]), dispatch_s=0,
                         paths={op["path"]: dict(warm_GBps=profile["directed_path_rates"][op["path"]], operation_s=0, first_pair_s=0) for op in plan["ops"]},
                         scope="Symbolic isolated path durations and worker-order DAG; shared contention unmodeled")
            s = estimate_execution(plan, case["workers"], model, new_state(case["workers"]))
            finishes = {r:0.0 for r in range(4)}
            times = {row["op_id"]:row["end_s"] for row in s["timeline"]}
            for op in plan["ops"]:
                finishes[op["tp"]] = max(finishes[op["tp"]],times[op["op_id"]])
            assert abs(max(finishes.values())-s["estimated_execute_s"]) < 1e-8
            service_cache[signature] = (s,finishes)
        return service_cache[signature]
    def key(selected):
        s, finishes = service(selected)
        return (round(s["estimated_execute_s"], 10), tuple(sorted((round(finishes[r],10) for r in range(4)), reverse=True)),
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
        s, finishes = service(selected)
        last, queue = {}, []
        for index, op in enumerate(plan["ops"]):
            involved = {op["src_w"], op["dst_w"]}
            queue.append(dict(queue_position=index, op=op, depends_on_queue_positions=sorted({last[w] for w in involved if w in last})))
            for w in involved:
                last[w] = index
        return dict(selected_ids=list(ids(selected)), resource_lp_bound_symbolic_time=e["objective_s"],
                    rank_key=key(selected), ledger=layout_ledger(case,selected), plan=plan,
                    common_cpu_primary_symbolic_time=s["estimated_execute_s"],target_receiver_finish_symbolic_times=finishes,
                    executable_queue=queue, worker_dependency_score=s, predicted_real_T_ready_s=None,
                    shared_resource_finite_schedule_verified=False)
    fastest = []
    wm = {w["worker"]:w for w in case["workers"]}
    for rank in range(4):
        target = case["restore_scenarios"][0]["targets"][rank]
        options = [c for c in candidates if c["tp"] == rank and feasible(fastest+[c])]
        if options:
            chosen = min(options, key=lambda c:(-path_for(c,target,wm,profile)["GBps"], path_for(c,target,wm,profile)["distance"], c["worker"],c["id"]))
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
    policies["existing_strong_simple"].update(winner=simple["winner"], controls={name:dict(score(row["selected"]),steps=row.get("steps")) for name,row in simple["controls"].items()})
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
    service_best = min(finite,key=lambda r:(round(r["common_cpu_primary_symbolic_time"],10),r["ledger"]["allocated_hbm_bytes"],r["selected_ids"]))
    service_budget_rows = []
    for cap in range(5):
        allowed = [r for r in finite if r["ledger"]["allocated_hbm_bytes"] <= cap*UNIT]
        representative = min(allowed,key=lambda r:(round(r["common_cpu_primary_symbolic_time"],10),r["ledger"]["allocated_hbm_bytes"],r["selected_ids"]))
        service_budget_rows.append(dict(allowed_cache_hbm_bytes=cap*UNIT,actual_cache_hbm_bytes=representative["ledger"]["allocated_hbm_bytes"],
            common_cpu_primary_symbolic_time=representative["common_cpu_primary_symbolic_time"],selected_ids=representative["selected_ids"]))
    route_witness = {}
    witness_layouts = dict(sequential_full_cache=["cache/r0/pool0","cache/r1/pool1","cache/r2/pool0","cache/r3/pool1"],
                          flexible_rank_exchange=["cache/r0/pool1","cache/r1/pool1","cache/r2/pool0","cache/r3/pool0"])
    for name, selected_ids in witness_layouts.items():
        selected = [by_id[rid] for rid in selected_ids]
        plan = interleave(restore_plan(case["base"]+selected,case["restore_scenarios"][0]["targets"],case["workers"],profile,"fastest"))
        model = dict(schema_version=1,executor="serial",topology_id=topology_id(case["workers"]),dispatch_s=0,
                     paths={op["path"]:dict(warm_GBps=profile["directed_path_rates"][op["path"]],operation_s=0,first_pair_s=0) for op in plan["ops"]},
                     scope="Preregistered explicit whole-block route witness; symbolic fixed queue only, not a common-policy baseline or oracle")
        s = estimate_execution(plan,case["workers"],model,new_state(case["workers"]))
        times = {row["op_id"]:row["end_s"] for row in s["timeline"]}
        finishes = {r:max(times[op["op_id"]] for op in plan["ops"] if op["tp"] == r) for r in range(4)}
        last,queue = {},[]
        for index,op in enumerate(plan["ops"]):
            involved = {op["src_w"],op["dst_w"]}
            queue.append(dict(queue_position=index,op=op,depends_on_queue_positions=sorted({last[w] for w in involved if w in last})))
            for w in involved:
                last[w] = index
        route_witness[name] = dict(selected_ids=selected_ids,ledger=layout_ledger(case,selected),plan=plan,
            executable_queue=queue,worker_dependency_score=s,target_receiver_finish_symbolic_times=finishes,
            witness_symbolic_time=s["estimated_execute_s"],expected_symbolic_time=11/12 if name == "sequential_full_cache" else 5/6,
            scope="Frozen explicit route witness; original fastest restore route, same full DRAM opportunities, whole-block queue. Not a baseline replacement or best-result selection.")
    return dict(case_id=case["case_id"],policies=policies,finite_layouts=finite,route_witness=route_witness,
                finite_reference=dict(feasible_count=len(finite),best_selected_ids=best["selected_ids"],
                    best_resource_lp_bound_symbolic_time=best["resource_lp_bound_symbolic_time"],
                    minimum_cache_hbm_bytes=best["ledger"]["allocated_hbm_bytes"],scope=RULES["finite_reference"]),
                finite_service_reference=dict(scope="Fixed original resource_lp policy plus original isolated worker DAG; finite layout reference, not a baseline or shared-resource schedule optimum",
                    best_selected_ids=service_best["selected_ids"],best_common_cpu_primary_symbolic_time=service_best["common_cpu_primary_symbolic_time"],
                    actual_cache_hbm_bytes=service_best["ledger"]["allocated_hbm_bytes"],budget_frontier=service_budget_rows),
                pareto=pareto,real_joint_advantage_proven=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out",type=Path,required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    case,profile,paths = fixture()
    sources = [Path(__file__),TP4_SOURCE,ROOT/"codex_coding/results/v0.1.23.3/task2/tp4_scenarios/contract_cpu_r03/contract.json"]
    prior = ROOT/"codex_coding/results/v0.1.23.3/task2/placement_competition/case_cpu_r03/contract.json"
    derivation = ROOT/"codex_coding/results/v0.1.23.3/task2/placement_competition/case_active_preregister_r01/derivation.md"
    sources += [prior,derivation,derivation.with_name("route_witness.md"),Path(__file__).with_name("build_case.py")]
    sources += [ROOT/"main_lib/fluidckpt"/p for p in ("benchmarks/frozen_layout.py","benchmarks/topology_cases.py","cluster/planning.py","cluster/multi_joint.py","cluster/service_cost.py","cluster/path_catalog.py")]
    contract = dict(schema_version="placement_competition.symbolic_contract.v1",development="v0.1.23.3/task2",
        created_at_utc=datetime.now(timezone.utc).isoformat(),stage="preregistered_before_first_solve",synthetic=True,
        case=case,symbolic_profile=profile,path_contract=paths,baseline_rules=RULES,
        budget_ledger=dict(block_bytes=UNIT,allocation_quantum_bytes=QUANTUM,routing_quantum_bytes=UNIT,
            base_dram_bytes=4*UNIT,target_hbm_bytes=4*UNIT,max_cache_hbm_bytes=4*UNIT,
            cache_pool_caps_bytes=[2*UNIT,2*UNIT],staging_hbm_bytes=6*(64 << 20),runtime_bytes=None,
            physical_peak_bytes=None,physical_budget_admitted=False,physical_payload_allocated_bytes=0),
        competition_witness=dict(scarce_pool=0,high_quality_dependent_ranks=[0,2,3],demand_units=3,capacity_units=2,
            flexible_rank=0,flexible_alternative_rate=3,dependent_alternative_rates=[1.5,1.5],
            inequalities=["3 high-quality block demands > 2 pool0 slots","r0 alternative3 > r2/r3 alternative1.5","1 < v=3/2 < 12/7"],
            role_reuse="Target0/1 also cache source pools; cross-role worker dependencies differ from previous eight-worker TP4."),
        decision_contract=dict(full_dram_fallback_for_all=True,duplicate_copies_allowed=True,relay=False,
            schedule="Original rank_interleaved ops with shared source/destination worker ready dependencies",
            primary="Common original service_cost isolated worker-order DAG all-target receiver completion, symbolic dimensionless; resource bound separately reported",
            secondary="Descending target last receiver finish vector then cache bytes; later source use is not target weight readiness",
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
    contract["revision_contract"] = dict(prior_contract_path=str(prior.relative_to(ROOT)),prior_contract_sha256=json.loads(prior.read_text())["contract_sha256"],
        derivation_path=str(derivation.relative_to(ROOT)),derivation_sha256=sha(derivation),parameter_sweep=False,
        changes=[dict(candidate_id=f"cache/r{r}/pool1",old_symbolic_rate=.5,new_symbolic_rate=1.5) for r in (2,3)],
        reason="Prior rigid-pool1 rates were dominated by full DRAM fallback; satisfy two meaningful save locations per block without changing caps, rules, source opportunities or queue.",
        preregistered_expected_fastest_fixed_cache_queue=11/12,preregistered_expected_exchange_fixed_cache_queue=5/6,
        routes_must_be_independently_verified=True,joint_win_required=False)
    contract["revision_contract"]["route_witness_rules"] = dict(sequential_full_cache=["cache/r0/pool0","cache/r1/pool1","cache/r2/pool0","cache/r3/pool1"],
        flexible_rank_exchange=["cache/r0/pool1","cache/r1/pool1","cache/r2/pool0","cache/r3/pool0"],
        routing="Original restore_plan fastest, rank_interleaved, full DRAM retained; two fixed preregistered route witnesses only, not baseline substitution or result selection.")
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
        used = {op["replica_id"] for row in comparison["finite_layouts"] for op in row["plan"]["ops"]}
        checks["all_eight_candidates_used_by_some_legal_common_route"] = all(c["id"] in used for c in case["candidates"])
        checks["fastest_and_exchange_actual_same_16MiB"] = comparison["policies"]["capacity_aware_fastest"]["ledger"]["allocated_hbm_bytes"] == comparison["policies"]["balanced_exchange"]["ledger"]["allocated_hbm_bytes"] == 4*UNIT
        old = json.loads(prior.read_text())
        revised_old_profile = copy.deepcopy(old["symbolic_profile"])
        for c in case["candidates"]:
            if c["tp"] in (2,3) and c["worker"] == 1:
                revised_old_profile["directed_path_rates"][path_key(c["worker"],c["tier"],c["numa"],c["tp"],"hbm")] = 1.5*UNIT/1e9
        checks["only_two_declared_path_rates_changed"] = case == old["case"] and profile == revised_old_profile
        checks["two_preregistered_route_witness_formulas"] = all(abs(r["witness_symbolic_time"]-r["expected_symbolic_time"]) < 1e-8 for r in comparison["route_witness"].values())
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
