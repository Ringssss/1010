"""Build a symbolic retention-tier contract without scheduling or allocating payloads."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
Q = 4 << 20


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def make_contract():
    workers = []
    target_by_worker = {0: 0, 2: 1, 4: 2, 5: 3}
    for i in range(6):
        workers.append(dict(id=f"w{i}", node=f"n{i//2}", gpu=f"g{i%2}",
                            numa=f"u{i%2}", target_rank=target_by_worker.get(i),
                            role="target_and_optional_source" if i in target_by_worker else "optional_source_helper",
                            process_reuse="one logical worker per GPU; actual reuse unqualified",
                            physical_binding=None))
    locations = []
    for w in workers:
        for tier in ("hbm", "dram"):
            locations.append(dict(id=f"{tier}/{w['id']}", tier=tier, node=w["node"],
                                  numa=w["numa"], worker=w["id"], gpu=w["gpu"] if tier == "hbm" else None,
                                  role="optional_retention", physical_binding=None))
    for n in range(3):
        locations.append(dict(id=f"ssd/n{n}", tier="ssd", node=f"n{n}", numa="u0",
                              worker=f"w{2*n}", gpu=None, role="fixed_authoritative", physical_binding=None))
    blocks = []
    for rank in range(4):
        for j in range(2):
            i = 2 * rank + j
            ssd_node = 0 if i < 4 else 1 if i < 6 else 2
            blocks.append(dict(id=f"b{i}", identity="symbolic_processed_tp4_snapshot_v1",
                               rank=rank, offset_bytes=j*Q, nbytes=Q,
                               target_worker=["w0", "w2", "w4", "w5"][rank],
                               authoritative_location=f"ssd/n{ssd_node}",
                               payload_digest=None, generation=1, byte_interpretation="opaque runtime-ready TP byte range"))
    lm = {l["id"]: l for l in locations}
    wm = {w["id"]: w for w in workers}
    candidates, paths = [], []
    for b in blocks:
        legal = [l for l in locations if l["tier"] != "ssd" or l["id"] == b["authoritative_location"]]
        for l in legal:
            cid = f"{b['id']}@{l['id']}"
            fixed = l["tier"] == "ssd"
            candidates.append(dict(id=cid, block=b["id"], location=l["id"], nbytes=b["nbytes"],
                                   fixed=fixed, state="conceptually_valid_not_materialized",
                                   pause_charge_bytes=0 if fixed else b["nbytes"]))
            t = wm[b["target_worker"]]
            ids = [f"worker_service/{l['worker']}", f"worker_service/{t['id']}",
                   f"gpu_copy/{t['id']}", f"pci/{t['node']}"]
            if l["tier"] == "hbm":
                ids += [f"gpu_copy/{l['worker']}", f"pci/{l['node']}"]
            else:
                ids += [f"numa_dram/{l['node']}/{l['numa']}"]
                if l["worker"] != t["id"]:
                    ids += [f"gpu_copy/{l['worker']}", f"pci/{l['node']}",
                            f"host_staging/{l['node']}/{l['numa']}"]
            if l["tier"] == "ssd":
                ids += [f"ssd_queue/{l['node']}", f"host_staging/{l['node']}/{l['numa']}",
                        f"http_read_cpu/{l['node']}", f"http_loopback/{l['node']}"]
            if l["node"] != t["node"]:
                ids += [f"nic_out/{l['node']}", f"nic_in/{t['node']}", "fabric/three_node"]
            if l["node"] == t["node"] and l["numa"] != t["numa"]:
                ids += [f"numa_link/{l['node']}"]
            paths.append(dict(id=f"direct/{cid}", candidate=cid, source_worker=l["worker"],
                              target_worker=t["id"], target_rank=b["rank"], block=b["id"],
                              kind=f"{l['tier']}_to_hbm", cross_node=l["node"] != t["node"],
                              cross_numa_same_node=l["node"] == t["node"] and l["numa"] != t["numa"],
                              resource_incidence=[dict(resource=r, coefficient=1) for r in sorted(set(ids))],
                              pipeline=("SSD_service -> local_HTTP -> host_staging -> source_gpu_staging -> RCCL_unicast -> target_hbm" if fixed and l["worker"] != t["id"] else
                                        "dram -> host_staging -> source_gpu_staging -> RCCL_unicast -> target_hbm" if l["tier"] == "dram" and l["worker"] != t["id"] else
                                        "SSD_service -> local_HTTP -> host_staging -> target_gpu_copy" if fixed else
                                        "direct_unicast_or_local_copy -> target_hbm"),
                              ssd_legs=(dict(storage_to_proxy=dict(storage_node=l["node"], proxy_node=l["node"],
                                        proxy_worker=l["worker"], transport="local_HTTP_loopback_assumption",
                                        cross_node=False, physical_binding=None),
                                        proxy_to_target=dict(proxy_worker=l["worker"], target_worker=t["id"],
                                        transport="same_worker_host_to_GPU" if l["worker"] == t["id"] else "RCCL_GPU_staging",
                                        cross_node=l["node"] != t["node"], physical_binding=None)) if fixed else None),
                              backend_scope="existing staged primitive resource hypothesis; local DRAM owner metadata does not imply separate source GPU copy; exact path still unqualified",
                              rate_bytes_per_second=None, fixed_latency_seconds=None,
                              physical_path_binding=None))
    resource_ids = sorted({x["resource"] for p in paths for x in p["resource_incidence"]})
    resources = [dict(id=r, kind=r.split("/")[0], capacity_bytes_per_second=None,
                      physical_identity=None, status="logical_hypothesis_unmeasured") for r in resource_ids]
    # Each witness has one source per block; no route optimizer is invoked.
    def witness(hbm, dram):
        chosen = {f"b{i}": f"hbm/w{w}" for i, w in hbm.items()}
        chosen.update({f"b{i}": f"dram/w{w}" for i, w in dram.items()})
        return [dict(block=b["id"], candidate=f"{b['id']}@{chosen.get(b['id'], b['authoritative_location'])}",
                     nbytes=b["nbytes"]) for b in blocks]
    phases = []
    spec = [
        ("C0", [2,0,2,0,2,2], [2]*6, "isolated_control", {0:0,1:0,2:2,3:2,4:4,5:4,6:5,7:5}, {}, "HBM alone can retain the full snapshot"),
        ("C1", [1,0,1,0,1,1], [2]*6, "isolated_control", {0:0,2:2,4:4,6:5}, {1:0,3:2,5:4,7:5}, "HBM scarce; SSD-free coverage needs DRAM"),
        ("C2a", [1,1,0,1,1,0], [2,0,1,1,0,2], "isolated_control", {0:0,1:3,2:1,4:4}, {3:0,5:3,6:5,7:5}, "only nonuniform memory budgets differ from C1"),
        ("C2b", [1,1,0,1,1,0], [2,0,1,1,0,2], "shared_node_numa", {0:0,1:3,2:1,4:4}, {3:0,5:3,6:5,7:5}, "only resource sharing differs from C2a"),
        ("C3", [1,1,0,1,1,0], [1,0,1,0,0,1], "shared_node_numa", {0:0,1:3,2:1,4:4}, {3:0,5:2,6:5}, "aggregate memory shortage forces SSD-source bytes"),
    ]
    for name, hc, dc, mode, h, d, purpose in spec:
        caps = {f"hbm/w{i}": v*Q for i,v in enumerate(hc)}
        caps.update({f"dram/w{i}": v*Q for i,v in enumerate(dc)})
        incidence = {}
        for p in paths:
            # The isolated control keeps worker order but separates shared physical workpoints.
            incidence[p["id"]] = [x["resource"] if mode == "shared_node_numa" or x["resource"].startswith("worker_service/")
                                  else f"isolated/{p['id']}/{x['resource']}" for x in p["resource_incidence"]]
        phases.append(dict(id=name, purpose=purpose, capacity_bytes=caps,
                           aggregate_capacity_bytes=dict(hbm=sum(hc)*Q, dram=sum(dc)*Q),
                           sharing_mode=mode, constraint_resource_incidence=incidence,
                           witness=witness(h,d), fallback_policy="every authoritative SSD candidate remains legal",
                           selected_by_planner=False, schedule=None, predicted_ready_seconds=None))
    return dict(schema_version="multitier_scenarios.retention_contract.v1", development="v0.1.23.3/task2",
                created_utc=datetime.now(timezone.utc).isoformat(),
                logical_tp=4, block_unit_bytes=Q, payload_bytes=8*Q,
                scope="one paused model; one fixed TP4 restore target set; offline structural contract",
                snapshot=dict(identity="symbolic_processed_tp4_snapshot_v1", actual_model_binding=None,
                              source="already processed runtime-ready fixed TP4 bytes",
                              cold_checkpoint_transform="outside this restore graph; preprocessing/packing/sharding costs are not zeroed or measured",
                              repartitioning_allowed=False, model_payload_materialized=False),
                workers=workers, locations=locations, blocks=blocks, candidates=candidates,
                supergraph=dict(paths=paths, resources=resources,
                                incidence_semantics="a listed shared resource is consumed once per byte; resource IDs are aggregate constraints, not additive path rates",
                                directionality="NIC ingress/egress are separate logical hypotheses, physical duplex coupling unknown",
                                direct_unicast_only=True, relay_allowed=False, compression_allowed=False,
                                multicast_allowed=False, queue_policy=None),
                phases=phases,
                retention_accounting=dict(optional_tiers=["hbm","dram"],
                     ssd_authoritative_bytes_by_location={"ssd/n0":4*Q,"ssd/n1":2*Q,"ssd/n2":2*Q},
                     ssd_authoritative_bytes=8*Q, ssd_duplicate_bytes=0,
                     authoritative_retirement_allowed=False,
                     target_hbm_restore_bytes_by_worker={"w0":2*Q,"w2":2*Q,"w4":2*Q,"w5":2*Q},
                     target_hbm_restore_bytes=8*Q, target_hbm_charged_to_pause_budget=False,
                     peak_policy="conservative retention + target allocation + staging + runtime; no alias credit until actual adoption is certified",
                     staging_bytes_by_worker=None, runtime_bytes_by_worker=None,
                     declared_executor_staging_bytes_by_worker={f"w{i}":64<<20 for i in range(6)},
                     declared_executor_staging_bytes=6*(64<<20),
                     declared_executor_staging_bytes_by_node={f"n{i}":128<<20 for i in range(3)},
                     declared_executor_staging_tier="hbm",
                     staging_reservation_scope="conditional_on_original_serial_ClusterWorker; known conservative reservation, not actual allocation",
                     staging_allocated=False,
                     layout_transition_peak_bytes=None, physical_peak_bytes=None,
                     os_page_cache_policy="unknown; SSD-source bytes do not imply physical disk reads; warm/cold and page-cache accounting need a later protocol",
                     historical_physical_dram_limit_bytes_per_node=2<<30,
                     symbolic_capacity_is_operational_quota=False),
                physical_parameters=dict(status="unknown_not_calibrated", topology_binding=None,
                     rates_bytes_per_second=None, latencies_seconds=None, runtime_budget_admission=None,
                     ssd_backing_type=None, note="logical per-node SSD assumptions do not certify local disks; existing model artifacts may be on shared NFS"),
                actions=dict(all_policies_share_candidates=True, all_policies_share_fallback=True,
                     replication_allowed=True, replica_charge="each optional copy charged in full to its tier and location",
                     ssd_replication_allowed=False, arbitrary_future_targets_implemented=False),
                readiness=dict(structural_ready=False, physical_ready=False, cost_ready=False,
                     runtime_budget_ready=False, actual_model_ready=False, schedule_ready=False,
                     performance_ready=False), gpu_command=None)


def validate(contract):
    checks = []
    def check(name, condition, evidence=None):
        checks.append(dict(name=name, passed=bool(condition), evidence=evidence))
    blocks = {b["id"]:b for b in contract["blocks"]}
    locs = {l["id"]:l for l in contract["locations"]}
    candidates = {c["id"]:c for c in contract["candidates"]}
    paths = contract["supergraph"]["paths"]
    pm = {p["candidate"]:p for p in paths}
    resources = {r["id"] for r in contract["supergraph"]["resources"]}
    payload = sum(b["nbytes"] for b in blocks.values())
    check("finite_5_to_12_blocks", 5 <= len(blocks) <= 12)
    check("unique_block_location_candidate_path_ids", len(blocks)==len(contract["blocks"]) and len(locs)==len(contract["locations"]) and len(candidates)==len(contract["candidates"]) and len(pm)==len(paths))
    check("payload_exact_bytes", payload == contract["payload_bytes"])
    check("three_nodes_two_gpu_roles_each", Counter(w["node"] for w in contract["workers"]) == {"n0":2,"n1":2,"n2":2})
    check("tp4_four_unique_target_workers", len({b["target_worker"] for b in blocks.values()})==4 and {b["rank"] for b in blocks.values()}==set(range(4)))
    for rank in range(4):
        spans = sorted((b["offset_bytes"], b["nbytes"]) for b in blocks.values() if b["rank"]==rank)
        end = 0
        good = True
        for start,n in spans:
            good = good and start==end and n>0
            end = start+n
        check(f"rank{rank}_contiguous_exact_intervals",good)
    for c in candidates.values():
        b = blocks[c["block"]]
        l = locs[c["location"]]
        check(f"candidate/{c['id']}",c["nbytes"]==b["nbytes"] and c["id"] in pm and
              ((c["fixed"] and l["tier"]=="ssd" and l["id"]==b["authoritative_location"] and c["pause_charge_bytes"]==0) or
               (not c["fixed"] and l["tier"] in ("hbm","dram") and c["pause_charge_bytes"]==b["nbytes"])))
    for p in paths:
        ids = [x["resource"] for x in p["resource_incidence"]]
        check(f"path/{p['id']}",len(ids)==len(set(ids)) and set(ids)<=resources and all(x["coefficient"]==1 for x in p["resource_incidence"]) and p["rate_bytes_per_second"] is None)
    fixed = [c for c in candidates.values() if c["fixed"]]
    check("one_authoritative_ssd_source_per_block",Counter(c["block"] for c in fixed)=={b:1 for b in blocks})
    check("all_optional_locations_active_in_some_phase",all(any(p["capacity_bytes"][l]>=min(b["nbytes"] for b in blocks.values()) for p in contract["phases"]) for l,v in locs.items() if v["tier"] in ("hbm","dram")))
    fixed_by_location = Counter()
    for c in fixed:
        fixed_by_location[c["location"]] += c["nbytes"]
    check("ssd_authoritative_ledger_exact",dict(fixed_by_location)==contract["retention_accounting"]["ssd_authoritative_bytes_by_location"] and sum(fixed_by_location.values())==payload)
    phase_reports = []
    for phase in contract["phases"]:
        selected = phase["witness"]
        usage, tier_usage = Counter(), Counter()
        valid = Counter(x["block"] for x in selected)=={b:1 for b in blocks}
        for x in selected:
            c = candidates.get(x["candidate"])
            valid = valid and c is not None and c["block"]==x["block"] and x["nbytes"]==blocks[x["block"]]["nbytes"]
            if c:
                tier = locs[c["location"]]["tier"]
                tier_usage[tier] += c["nbytes"]
                if not c["fixed"]:
                    usage[c["location"]] += c["nbytes"]
        caps = phase["capacity_bytes"]
        valid = valid and all(n <= caps.get(l,-1) for l,n in usage.items())
        check(f"phase/{phase['id']}/witness_exact_coverage_budget",valid,dict(tier_usage))
        check(f"phase/{phase['id']}/full_ssd_always_legal",all(f"{b}@{blocks[b]['authoritative_location']}" in candidates for b in blocks))
        check(f"phase/{phase['id']}/exact_tier_capacity",all(sum(v for l,v in caps.items() if locs[l]["tier"]==t)==phase["aggregate_capacity_bytes"][t] for t in ("hbm","dram")))
        check(f"phase/{phase['id']}/all_paths_same_opportunity",set(phase["constraint_resource_incidence"])=={p["id"] for p in paths})
        h = phase["aggregate_capacity_bytes"]["hbm"]
        d = phase["aggregate_capacity_bytes"]["dram"]
        lower = max(0,payload-h-d)
        phase_reports.append(dict(id=phase["id"], hbm_capacity_bytes=h,dram_capacity_bytes=d,
             witness_tier_bytes=dict(tier_usage), witness_optional_bytes=sum(usage.values()),
             ssd_source_logical_bytes_lower_bound=lower,
             dram_bytes_necessary_if_no_ssd=max(0,payload-h),
             hbm_bytes_necessary_if_no_ssd=max(0,payload-d),
             witness_optional_usage_by_location=dict(usage),
             conservative_known_hbm_peak_bytes=payload+tier_usage["hbm"]+contract["retention_accounting"]["declared_executor_staging_bytes"],
             known_hbm_peak_excludes="runtime/transition; includes conditional declared executor staging; alias not credited",
             schedule_feasibility="not checked; rates, queues and simultaneous service unknown"))
    phases = {p["id"]:p for p in contract["phases"]}
    check("C2a_C2b_only_sharing_changed",{k:v for k,v in phases["C2a"].items() if k not in ("id","purpose","sharing_mode","constraint_resource_incidence")}=={k:v for k,v in phases["C2b"].items() if k not in ("id","purpose","sharing_mode","constraint_resource_incidence")})
    check("C1_C2a_sharing_fixed",phases["C1"]["constraint_resource_incidence"]==phases["C2a"]["constraint_resource_incidence"])
    check("C2b_C3_sharing_hbm_fixed",phases["C2b"]["constraint_resource_incidence"]==phases["C3"]["constraint_resource_incidence"] and all(phases["C2b"]["capacity_bytes"][l]==phases["C3"]["capacity_bytes"][l] for l in locs if locs[l]["tier"]=="hbm"))
    check("C3_ssd_source_lower_bound_exact_q",phase_reports[-1]["ssd_source_logical_bytes_lower_bound"]==contract["block_unit_bytes"] and phase_reports[-1]["witness_tier_bytes"].get("ssd")==contract["block_unit_bytes"])
    check("C2_and_C3_both_helpers_serve_cross_node_hbm",all(all(any(x["candidate"].endswith(f"@hbm/{w}") and pm[x["candidate"]]["cross_node"] for x in phases[p]["witness"]) for w in ("w1","w3")) for p in ("C2a","C2b","C3")))
    accounting = contract["retention_accounting"]
    check("serial_staging_reservation_64MiB_times_six",accounting["declared_executor_staging_bytes"]==6*(64<<20) and sum(accounting["declared_executor_staging_bytes_by_worker"].values())==accounting["declared_executor_staging_bytes"] and sum(accounting["declared_executor_staging_bytes_by_node"].values())==accounting["declared_executor_staging_bytes"] and accounting["staging_allocated"] is False)
    check("ssd_HTTP_and_RCCL_legs_separate",all(p["ssd_legs"] is not None and p["ssd_legs"]["storage_to_proxy"]["storage_node"]==locs[candidates[p["candidate"]]["location"]]["node"] and p["ssd_legs"]["storage_to_proxy"]["cross_node"] is False and p["ssd_legs"]["proxy_to_target"]["target_worker"]==p["target_worker"] and any(x["resource"].startswith("http_read_cpu/") for x in p["resource_incidence"]) for p in paths if candidates[p["candidate"]]["fixed"]))
    shared = phases["C2b"]["constraint_resource_incidence"]
    counts = Counter(r for ids in shared.values() for r in ids)
    check("shared_nic_numa_ssd_gpu_resources_reused",all(any(r.startswith(prefix) and n>1 for r,n in counts.items()) for prefix in ("nic_out/","nic_in/","numa_dram/","ssd_queue/","gpu_copy/","pci/")))
    check("physical_and_cost_unknown",all(v is False for k,v in contract["readiness"].items() if k!="structural_ready") and contract["gpu_command"] is None and contract["physical_parameters"]["rates_bytes_per_second"] is None)
    return dict(passed=all(x["passed"] for x in checks), check_count=len(checks),checks=checks,
                phase_reports=phase_reports,method="linear contract consistency + supplied exact-byte witnesses; no exhaustive placement enumeration or solver",
                layer_meaning="capacity necessity and feasible witnesses; no optimal tier choice, dominance or latency benefit claimed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out",required=True,type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit("Output directory already exists; choose a fresh run ID")
    contract = make_contract()
    result = validate(contract)
    from jsonschema import Draft202012Validator
    schema = json.loads(Path(__file__).with_name("schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(contract)
    if not result["passed"]:
        raise SystemExit("Contract validation failed: "+str([x["name"] for x in result["checks"] if not x["passed"]]))
    contract["readiness"]["structural_ready"] = True
    contract["contract_hash"] = canonical(contract)
    args.out.mkdir(parents=True)
    write(args.out/"contract.json",contract)
    write(args.out/"structural_checks.json",result)
    sources = [Path(__file__),Path(__file__).with_name("schema.json"),Path(__file__).with_name("README.md"),
               ROOT/"AGENTS.md",ROOT/"docx/debug_philosophy.md",ROOT/"docx/01_paper_scope_management_guide.md",
               ROOT/"docx/articles/2026-10-10_joint_planning_necessity_and_boundaries.md",
               ROOT/"docx/plans/2026-10-10_multiple_placement_competition.md",
               ROOT/"main_lib/fluidckpt/cluster/worker.py",ROOT/"main_lib/fluidckpt/memory/ssd.py"]
    write(args.out/"provenance.json",dict(contract_hash=contract["contract_hash"],
         sources=[dict(path=str(p.relative_to(ROOT)),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sources],
         gpu_command=None, ssh_executed=False, product_modified=False,payload_allocated=False))
    print(json.dumps(dict(out=str(args.out),contract_hash=contract["contract_hash"],checks=result["check_count"],passed=True)))


if __name__ == "__main__":
    main()
