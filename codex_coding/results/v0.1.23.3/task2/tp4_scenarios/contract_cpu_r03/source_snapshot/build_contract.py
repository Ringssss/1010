"""Freeze small symbolic TP4 cases and reuse existing CPU planners/scorers."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import itertools
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.benchmarks.frozen_layout import evaluate, layout_ledger, strong_simple
from fluidckpt.cluster.multi_joint import joint_scenarios
from fluidckpt.cluster.path_catalog import topology_id
from fluidckpt.cluster.planning import path_for, validate_plan
from fluidckpt.cluster.service_cost import estimate_execution, new_state

UNIT = 4 << 20
STAGING_PER_WORKER = 64 << 20
IDENTITY = "synthetic_tp4_two4MiB_blocks_not_model_weights"
PROOF = "codex_coding/results/v0.1.23.3/task2/theory_validation/proofs_cpu_r01/validation.json"
POLICIES = {
    "local_capacity_fill": "Visit candidates by (tp, off, id); add each if original layout_ledger accepts it. All candidates are target-local; no marginal scoring.",
    "strong_simple": "Unmodified frozen_layout.strong_simple; report all three controls and common resource_lp final score.",
    "bottleneck_balanced": "Start empty; evaluate every feasible singleton addition using original resource_lp. Key is (global bound, descending four target serial-service times). Accept strict lexicographic key improvement, including zero global gain. Choose minimum (key, candidate id); repeat until no improvement. No deletion, swaps, bundles or oracle lookahead.",
    "joint": "Unmodified multi_joint.joint_scenarios; original MILP then original resource_lp routing and byte rounding. No secondary objective or modified solver.",
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def make_case(case_id, caps, shared=False):
    # Reuse the recorded theory fixture; replace GiB extents with fixed small blocks.
    original = json.loads((ROOT / PROOF).read_text())["existing_planner_plateau"]["fixture"]
    workers = copy.deepcopy(original["workers"])
    for w in workers:
        i = w["worker"]
        w.update(node=f"symbolic_source_{i-4}" if i >= 4 else f"symbolic_target_{i}",
                 gpu=i-4 if i >= 4 else 0, numa=i,
                 session="offline_tp4_contract", epoch=f"symbolic_{i}",
                 used_bytes=dict(hbm=0, dram=2*UNIT if i >= 4 else 0),
                 budget_bytes=dict(hbm=caps[i]*UNIT if i < 4 else 0, dram=2*UNIT))
    if shared:
        workers[4]["node"] = workers[5]["node"] = "symbolic_U"
    def replica(tp, worker, off, n, tier, rid):
        return dict(id=rid, identity=IDENTITY, tp=tp, off=off, n=n, tier=tier,
                    worker=worker, node=workers[worker]["node"], gpu=workers[worker]["gpu"], numa=worker,
                    state="VALID", generation=1, leases=0)
    base = [replica(i, i+4, 0, 2*UNIT, "dram", f"base/{i}") for i in range(4)]
    candidates = [replica(i, i, j*UNIT, UNIT, "hbm", f"cache/{i}/block{j}")
                  for i in range(4) for j in range(2)]
    targets = [dict(key=f"target/{i}", identity=IDENTITY, tp=i, off=0, n=2*UNIT,
                    worker=i, tier="hbm") for i in range(4)]
    locations = {f"{i}:hbm": caps[i]*UNIT if i < 4 else 0 for i in range(8)}
    case = dict(case_id=case_id, workers=workers, base=base, candidates=candidates,
                restore_scenarios=[dict(name="all_four_simultaneously_required", weight=1,
                                        targets=targets)], objective="expected",
                budget=dict(tier_budgets={"hbm": sum(caps)*UNIT}, location_budgets=locations,
                            base_dram_bytes=8*UNIT, target_allocation_bytes=8*UNIT,
                            staging_bytes=8*STAGING_PER_WORKER))
    profile = dict(rates=dict(local_hbm=4*UNIT/1e9, local_dram=4*UNIT/1e9,
                              network=UNIT/1e9), gpu_ingress_GBps=100*UNIT/1e9,
                   gpu_egress_GBps=100*UNIT/1e9, dram_GBps=100*UNIT/1e9,
                   network_node_GBps=UNIT/1e9, network_pairs=[[i+4, i] for i in range(4)],
                   serial_target_execution=True,
                   provenance="SYMBOLIC_NORMALIZED_ONLY: 4MiB/unit, one unit per symbolic time; no hardware rate")
    return case, profile


def paths_resources(case, profile):
    wm = {w["worker"]: w for w in case["workers"]}
    paths, resources = [], {}
    source_nodes = {w["node"]: w["worker"]-4 for w in case["workers"] if w["worker"] >= 4}
    def semantic(raw):
        if raw.startswith("dram:"):
            return "source_dram/" + raw.rsplit(":", 1)[-1]
        if raw.startswith("net_out:"):
            node = raw.split(":", 1)[1]
            return "source_export/U" if node == "symbolic_U" else f"source_export/{source_nodes[node]}"
        if raw.startswith("net_in:"):
            return "target_network_in/" + raw.rsplit("_", 1)[-1]
        return raw
    for replica in case["base"] + case["candidates"]:
        target = case["restore_scenarios"][0]["targets"][replica["tp"]]
        p = path_for(replica, target, wm, profile)
        assert p is not None
        ids = []
        for raw, cap in p["resources"].items():
            rid = semantic(raw)
            ids.append(rid)
            resources.setdefault(rid, dict(ResourceID=rid, raw_planner_ids=[],
                                           physical_capacity_GBps=None,
                                           symbolic_capacity_units_per_time=cap*1e9/UNIT,
                                           physical_identity_status="unbound"))
            if raw not in resources[rid]["raw_planner_ids"]:
                resources[rid]["raw_planner_ids"].append(raw)
        paths.append(dict(path=p["path"], replica_id=replica["id"], source_worker=replica["worker"],
                          target_rank=target["tp"], target_worker=target["worker"], kind=p["kind"],
                          incidence=ids, symbolic_rate_units_per_time=p["GBps"]*1e9/UNIT,
                          physical_rate_GBps=None, physical_path_binding=None))
    return dict(paths=paths, resources=list(resources.values()),
                incidence_semantics="Each byte consumes every listed resource once; equal ResourceID means one shared constraint, never additive capacities.")


def descriptor(case, profile):
    caps = [case["budget"]["location_budgets"][f"{i}:hbm"] // UNIT for i in range(4)]
    return dict(case=case, symbolic_profile=profile, path_contract=paths_resources(case, profile),
                role_mapping=dict(target_workers=list(range(4)), source_workers=list(range(4, 8)),
                                  physical_nodes=None, physical_GPUs=None,
                                  source_groups=[[4, 5], [6, 7]],
                                  real_model_binding=False),
                budget_ledger=dict(scope="Externally prescribed cache budget only; no physical allocation or deployment authorization",
                    block_bytes=UNIT, target_blocks_per_rank=2, cache_units_by_target=caps,
                    base_dram_bytes=8*UNIT, target_hbm_bytes=8*UNIT,
                    max_cache_hbm_bytes=sum(caps)*UNIT,
                    staging_hbm_bytes_per_worker=STAGING_PER_WORKER,
                    staging_hbm_bytes=8*STAGING_PER_WORKER, runtime_bytes=None,
                    staging_status="Known conservative execution reservation: 64MiB per logical worker, 512MiB total; unallocated and not physically admitted",
                    runtime_unknown_reason="Worker runtime must be independently admitted under unchanged 2GiB/node historical budget; symbolic eight workers are not deployable evidence",
                    physical_peak_bytes=None, physical_budget_admitted=False),
                decision_boundary=dict(candidate_opportunity="All eight target-local block candidates, including those excluded by zero location capacity; no remote-HBM placement candidate in this phase",
                    scope="Capacity and bottleneck-baseline contract only; not general remote-placement assignment or proof of complex joint benefit",
                    fallback="Full immutable per-rank DRAM base at source rank+4, always available",
                    routing="Original resource_lp, rank_interleaved queue; direct unicast only",
                    relay_allowed=False, multicast_allowed=False, coding_compression_allowed=False),
                qualification=dict(functional_ready=False, prediction_ready=False,
                    physical_resource_incidence_ready=False, bandwidth_ready=False,
                    runtime_budget_ready=False, model_binding_ready=False,
                    formal_performance_ready=False, gpu_command=None))


def feasible(case, selected):
    try:
        layout_ledger(case, selected)
        return True
    except ValueError:
        return False


def check_contract(contract):
    from jsonschema import Draft202012Validator
    schema = json.loads(Path(__file__).with_name("schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(contract)
    assert contract["schema_version"] == "tp4_scenarios.symbolic_contract.v1"
    assert contract["logical_tp"] == 4 and contract["payload_bytes"] == 8*UNIT
    assert all(value is False for value in contract["readiness"].values())
    for row in contract["cases"]:
        case = row["case"]
        assert len(case["workers"]) == 8 and len(case["base"]) == 4
        assert len({(w["node"], w["gpu"]) for w in case["workers"]}) == 8
        assert len(case["candidates"]) == 8
        targets = case["restore_scenarios"][0]["targets"]
        assert {t["tp"] for t in targets} == set(range(4))
        assert {t["worker"] for t in targets} == set(range(4))
        assert len({c["id"] for c in case["candidates"]}) == 8
        wm = {w["worker"]: w for w in case["workers"]}
        for replica in case["base"] + case["candidates"]:
            assert all(replica[key] == wm[replica["worker"]][key] for key in ("node", "gpu", "numa"))
        assert sum(b["n"] for b in case["base"]) == 8*UNIT
        for target in targets:
            rank = target["tp"]
            base = case["base"][rank]
            assert base["tier"] == "dram" and base["worker"] == rank+4
            assert (base["off"], base["n"], base["identity"]) == (0, 2*UNIT, IDENTITY)
            candidates = sorted((c for c in case["candidates"] if c["tp"] == rank), key=lambda c: c["off"])
            assert [(c["off"], c["n"], c["worker"]) for c in candidates] == [(0, UNIT, rank), (UNIT, UNIT, rank)]
            assert all(c["identity"] == target["identity"] == IDENTITY for c in candidates)
        assert row["budget_ledger"]["staging_hbm_bytes"] == 512 << 20
        assert row["budget_ledger"]["runtime_bytes"] is None
        assert row["qualification"]["gpu_command"] is None
        assert all(v is False for k, v in row["qualification"].items() if k != "gpu_command")
    c1, c2 = contract["cases"][2:]
    assert c1["case"]["budget"] == c2["case"]["budget"]
    assert c1["symbolic_profile"] == c2["symbolic_profile"]
    # Normalize raw node-derived names to stable source-DRAM/export identities.
    left, right = c1["path_contract"]["paths"], c2["path_contract"]["paths"]
    assert len(left) == len(right) == 12
    for a, b in zip(left, right):
        expected = copy.deepcopy(a)
        expected["incidence"] = ["source_export/U" if rid in ("source_export/0", "source_export/1") else rid
                                 for rid in a["incidence"]]
        assert expected == b, (a, b)
    def changes(a, b, path=""):
        if isinstance(a, dict) and isinstance(b, dict):
            assert set(a) == set(b)
            return [r for key in a for r in changes(a[key], b[key], f"{path}.{key}" if path else key)]
        if isinstance(a, list) and isinstance(b, list):
            assert len(a) == len(b)
            return [r for i, (x, y) in enumerate(zip(a, b)) for r in changes(x, y, f"{path}[{i}]")]
        return [] if a == b else [dict(field=path, c1=a, c2=b)]
    changed = changes(c1["case"], c2["case"])
    expected_fields = ["workers[4].node", "workers[5].node", "base[0].node", "base[1].node", "case_id"]
    assert {row["field"] for row in changed} == set(expected_fields)
    aliases = []
    right_resources = {r["ResourceID"]: r for r in c2["path_contract"]["resources"]}
    for resource in c1["path_contract"]["resources"]:
        rid = resource["ResourceID"]
        new_rid = "source_export/U" if rid in ("source_export/0", "source_export/1") else rid
        other = right_resources[new_rid]
        assert resource["symbolic_capacity_units_per_time"] == other["symbolic_capacity_units_per_time"]
        if rid != new_rid or resource["raw_planner_ids"] != other["raw_planner_ids"]:
            aliases.append(dict(c1_ResourceID=rid, c2_ResourceID=new_rid,
                c1_raw_ids=resource["raw_planner_ids"], c2_raw_ids=other["raw_planner_ids"],
                sharing_relation_changed=rid != new_rid,
                interpretation="Two exports share one unchanged-capacity constraint" if rid != new_rid else "Node-label alias only; independent NUMA DRAM resource"))
    return dict(schema="Small TP4-specific validation; not the TP2 topology_cases.validate_case schema",
                json_schema_draft_2020_12_passed=True,
                normalized_incidence_only_export_sharing=True,
                c1_c2_changed_fields=changed, resource_alias_impact=aliases,
                symbolic_mapping_only=True, relocated_physical_workers=False,
                stable_dram_resources_independent=True, perworker_resources_unchanged=True,
                target_ingress_resources_unchanged=True, path_kind_and_rate_unchanged=True,
                application_budget_unchanged=True, staging_runtime_separate=True)


def rank_key(evaluation):
    plan = next(iter(evaluation["plans"].values()))
    target_times = plan["serial_target_times_s"]
    return (round(evaluation["objective_s"], 10),
            *sorted((round(target_times[i], 10) for i in range(4)), reverse=True))


def balanced(case, profile):
    selected, steps = [], []
    before = rank_key(evaluate(case, profile, selected))
    while True:
        choices = []
        for c in case["candidates"]:
            if c in selected or not feasible(case, selected+[c]):
                continue
            after = rank_key(evaluate(case, profile, selected+[c]))
            if after < before:
                choices.append((after, c["id"], c))
        if not choices:
            return selected, steps
        after, rid, c = min(choices, key=lambda row: row[:2])
        steps.append(dict(candidate_id=rid, before=list(before), after=list(after),
                          zero_global_gain=after[0] == before[0]))
        selected.append(c)
        before = after


def score(case, profile, selected):
    evaluation = evaluate(case, profile, selected)
    plan = next(iter(evaluation["plans"].values()))
    validate_plan(plan)
    assert plan["strategy"] == "resource_lp" and plan["fallback_reason"] is None
    wm = {w["worker"]: w for w in case["workers"]}
    replicas = {r["id"]: r for r in case["base"] + selected}
    model = dict(schema_version=1, executor="serial", topology_id=topology_id(case["workers"]),
                 dispatch_s=0, paths={}, scope="Symbolic isolated durations and worker-order DAG; shared-resource concurrent durations remain unmodeled")
    for op in plan["ops"]:
        p = path_for(replicas[op["replica_id"]], plan["targets"][op["tp"]], wm, profile)
        model["paths"][p["path"]] = dict(warm_GBps=p["GBps"], operation_s=0, first_pair_s=0)
    service = estimate_execution(plan, case["workers"], model, new_state(case["workers"]))
    last, queue = {}, []
    for position, op in enumerate(plan["ops"]):
        involved = {op["src_w"], op["dst_w"]}
        dependencies = sorted({last[w] for w in involved if w in last})
        queue.append(dict(queue_position=position, op=op, depends_on_queue_positions=dependencies))
        for w in involved:
            last[w] = position
    return dict(selected_ids=sorted(c["id"] for c in selected),
                resource_lp_bound_symbolic_time=evaluation["objective_s"],
                rank_key=list(rank_key(evaluation)), ledger=layout_ledger(case, selected),
                executable_queue=queue, worker_dependency_score=service,
                predicted_real_T_ready_s=None,
                shared_resource_finite_schedule_verified=False,
                queue_qualification="Coverage-valid abstract queue only; physical source epochs, allocations, transport, staging, contention and real model binding are absent")


def inspect_case(case, profile):
    local = []
    for c in sorted(case["candidates"], key=lambda c: (c["tp"], c["off"], c["id"])):
        if feasible(case, local+[c]):
            local.append(c)
    simple = strong_simple(case, profile)
    equalized, steps = balanced(case, profile)
    b = case["budget"]
    joint = joint_scenarios(case["base"], case["candidates"], case["restore_scenarios"],
                            case["workers"], profile, b["tier_budgets"], b["location_budgets"],
                            solver_options={"time_limit": 5})
    rows = {name: score(case, profile, selected) for name, selected in
            [("local_capacity_fill", local), ("strong_simple", simple["selected"]),
             ("bottleneck_balanced", equalized), ("joint", joint["selected"])]}
    rows["strong_simple"].update(winner=simple["winner"],
        controls={name: dict(selected_ids=sorted(c["id"] for c in row["selected"]),
                             resource_lp_bound_symbolic_time=row["evaluation"]["objective_s"])
                  for name, row in simple["controls"].items()})
    rows["bottleneck_balanced"]["steps"] = steps
    rows["joint"].update(solver_status=joint["status"],
        continuous_solver_bound_symbolic_time=joint["objective_s"],
        rounded_plan_symbolic_time=joint["rounded_plan_s"])
    finite = []
    candidates = case["candidates"]
    for count in range(len(candidates)+1):
        for subset in itertools.combinations(candidates, count):
            if not feasible(case, list(subset)):
                continue
            ev = evaluate(case, profile, list(subset))
            finite.append(dict(selected_ids=sorted(c["id"] for c in subset),
                               resource_lp_bound_symbolic_time=ev["objective_s"],
                               rank_key=list(rank_key(ev))))
    oracle = min(finite, key=lambda r: (round(r["resource_lp_bound_symbolic_time"], 10),
                                      len(r["selected_ids"]), r["selected_ids"]))
    continuous = joint["objective_s"]
    assert joint["status"] == "optimal"
    assert continuous <= oracle["resource_lp_bound_symbolic_time"] + 1e-8
    for row in rows.values():
        assert row["resource_lp_bound_symbolic_time"] >= oracle["resource_lp_bound_symbolic_time"]-1e-8
    for row in finite:
        row["cache_hbm_bytes"] = len(row["selected_ids"])*UNIT
    points = []
    for row in sorted(finite, key=lambda r: (round(r["resource_lp_bound_symbolic_time"], 10),
                                           r["cache_hbm_bytes"], r["selected_ids"])):
        dominated = any(other["cache_hbm_bytes"] <= row["cache_hbm_bytes"] and
            other["resource_lp_bound_symbolic_time"] <= row["resource_lp_bound_symbolic_time"]+1e-8 and
            (other["cache_hbm_bytes"] < row["cache_hbm_bytes"] or
             other["resource_lp_bound_symbolic_time"] < row["resource_lp_bound_symbolic_time"]-1e-8)
            for other in finite)
        if dominated or any(p["cache_hbm_bytes"] == row["cache_hbm_bytes"] and
                           abs(p["resource_lp_bound_symbolic_time"]-row["resource_lp_bound_symbolic_time"]) <= 1e-8
                           for p in points):
            continue
        points.append(row)
    for row in rows.values():
        minimum_cache = min(r["cache_hbm_bytes"] for r in finite
                            if r["resource_lp_bound_symbolic_time"] <= row["resource_lp_bound_symbolic_time"]+1e-8)
        row["cache_bytes_above_finite_minimum_at_no_worse_primary_cost"] = row["ledger"]["allocated_hbm_bytes"]-minimum_cache
    return dict(case_id=case["case_id"], policies=rows, finite_layouts=finite,
                base_only_reference=score(case, profile, []),
                space_primary_cost_pareto=dict(scope="Finite same-candidate, same fixed route-policy reference; cache HBM versus symbolic resource bound, not measured T_ready",
                                               nondominated_points=points,
                                               equal_point_tie="Lexicographically smallest selected ID list representative"),
                finite_oracle=dict(scope="Finite placement reference only; fixed existing resource_lp routing and byte rounding, no route-order search, not a baseline or true finite execution optimum",
                                   feasible_count=len(finite), best=oracle),
                equal_resource_lp_cost_groups=[dict(symbolic_time=value,
                    policies=[name for name, row in rows.items()
                              if round(row["resource_lp_bound_symbolic_time"], 10) == value])
                    for value in sorted({round(row["resource_lp_bound_symbolic_time"], 10) for row in rows.values()})],
                qualification="CPU symbolic contract check; equal scores or different equivalent layout IDs do not establish real joint benefit")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    source_paths = [Path(__file__), Path(__file__).with_name("schema.json"), ROOT / PROOF,
                    ROOT / "codex_coding/src/v0.1.23.3/task2/theory_validation/proofs/validate_proofs.py"]
    source_paths += [ROOT / "main_lib/fluidckpt" / name for name in
                     ("benchmarks/frozen_layout.py", "benchmarks/topology_cases.py",
                      "cluster/planning.py", "cluster/multi_joint.py", "cluster/service_cost.py")]
    fixtures = [make_case("C0_local_sufficient", [2, 2, 2, 2]),
                make_case("C1_equal_plateau_control", [1, 1, 1, 1]),
                make_case("C1_uneven_preregistered", [2, 1, 0, 1]),
                make_case("C2_shared_source_export", [2, 1, 0, 1], True)]
    contract = dict(schema_version="tp4_scenarios.symbolic_contract.v1", development="v0.1.23.3/task2",
        created_at_utc=datetime.now(timezone.utc).isoformat(), stage="symbolic_contract_physical_unknown",
        synthetic=True, logical_tp=4, unit_bytes=UNIT, payload_bytes=8*UNIT,
        actual_model_weight_binding=False, model_runtime_binding=None, allocated_payload_bytes=0,
        normalized_time_semantics="Dimensionless theoretical service unit; fields emitted by reused planners as seconds are not physical seconds. Rates are 4MiB/unit normalized coefficients only.",
        cases=[descriptor(*fixture) for fixture in fixtures], baseline_rules=POLICIES,
        c1_c2_control="Same candidate identities/content/placement opportunities and budgets, target/source worker IDs, path rates and capacities. Only source4/5 independent export ResourceIDs become one U. Raw node-derived DRAM names change, stable DRAM roles remain independent.",
        metric_contract=dict(primary_real_metric="controller weight-load-start to all target ranks filled/usable",
            real_start_event=None, real_end_event=None, real_clock=None,
            prediction_fields="resource bound, isolated worker-order symbolic score; neither is measured T_ready",
            model_binding_and_verification_time="unknown; record separately before real experiment"),
        unknowns=["physical role mapping under unchanged budget", "actual HCA/PCI/NUMA incidence",
                  "same-primitive path durations/cold state", "shared capacity/concurrent service",
                  "runtime and staging full peaks", "fresh source epochs/leases",
                  "actual model parameter binding", "real all-rank weight-ready time"],
        stopping_conditions=["Same real layout/routes after calibration: stop duplicate GPU policy comparison",
            "Strong simple/balanced baseline has same cost: report no joint decision gain; do not add favorable parameters",
            "Unknown physical shared path or capacity: keep physical readiness false",
            "Eight logical workers exceed original 2GiB/node runtime allowance: do not deploy or increase budget",
            "CPU fixture/schema failure: preserve failed run and diagnose fixed input before retry",
            "No authorized GPU/SSH run in this contract; any physical execution needs parent-managed admission"],
        gpu_launches=0, ssh_operations=0, product_changes=0,
        source_refs=[dict(path=str(p.relative_to(ROOT)), sha256=digest(p)) for p in source_paths],
        readiness=dict(physical_mapping=False, physical_capacity=False, finite_execution=False,
                       model_binding=False, real_performance=False), gpu_command=None)
    contract["contract_sha256"] = canonical(contract)
    write(args.out / "contract.json", contract)
    try:
        impact_audit = check_contract(contract)
        write(args.out / "c1_c2_impact_audit.json", impact_audit)
        comparisons = [inspect_case(*fixture) for fixture in fixtures]
        control = comparisons[1]
        assert control["policies"]["strong_simple"]["selected_ids"] == []
        assert abs(control["policies"]["bottleneck_balanced"]["resource_lp_bound_symbolic_time"]-1.25) < 1e-8
        assert abs(control["policies"]["joint"]["resource_lp_bound_symbolic_time"]-1.25) < 1e-8
        assert len(comparisons[0]["policies"]["local_capacity_fill"]["selected_ids"]) == 8
        assert fixtures[2][0]["budget"] == fixtures[3][0]["budget"]
        assert fixtures[2][1] == fixtures[3][1]
        expected_counts = [256, 81, 36, 36]
        assert [c["finite_oracle"]["feasible_count"] for c in comparisons] == expected_counts
        for p in source_paths:
            ref = next(r for r in contract["source_refs"] if r["path"] == str(p.relative_to(ROOT)))
            assert digest(p) == ref["sha256"]
        write(args.out / "comparison.json", dict(contract_sha256=contract["contract_sha256"],
              status="passed_under_symbolic_contract", cases=comparisons,
              checks=dict(coverage_and_budget=True, no_route_fallback=True,
                          expected_feasible_counts=expected_counts, plateau_baseline=True,
                          c1_c2_same_budget_rates=True, source_sha_unchanged=True),
              real_joint_advantage_proven=False, new_solver=False, gpu_launches=0))
    except Exception as exc:
        write(args.out / "failure.json", dict(error_type=type(exc).__name__, error=str(exc),
              stage="offline_comparison", preserved_contract=True, gpu_launches=0))
        raise
    summary = {c["case_id"]: {name: row["resource_lp_bound_symbolic_time"]
               for name, row in c["policies"].items()} for c in comparisons}
    write(args.out / "summary.json", summary)
    print(json.dumps(dict(out=str(args.out), status="passed_under_symbolic_contract", costs=summary)))


if __name__ == "__main__":
    main()
