"""Build an evidence-bound deployment ledger without loading product GPU code."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
RESULT = ROOT / "codex_coding/results/v0.1.23.3/task2"
GIB, MIB = 1 << 30, 1 << 20


def read(rel):
    return json.loads((ROOT / rel).read_text())


def ref(path):
    path = Path(path)
    return {"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if not out.is_relative_to(RESULT / "tp4_scenarios") or (out / "deployment_feasibility.json").exists():
        raise ValueError("require a new feasibility artifact in the task result subtree")
    out.mkdir(parents=True, exist_ok=True)
    old_guards = {n: read(f"codex_coding/results/v0.1.23.3/task2/decision_validation/measure_refresh_r01/guard_{n}/stdout.json")
                  for n in ("node28", "node30")}
    new_guards = {n: json.loads((out / ("metadata_" + n) / "stdout.json").read_text()) for n in old_guards}
    execution = read("codex_coding/results/v0.1.23.3/task2/layout_validation/execution_r01/execution.json")
    runtime = {row["node"]: row for row in execution["cases"][0]["layouts"]["A"]["budget_gate"]["resources"]}
    physical = read("codex_coding/results/v0.1.23.3/task2/three_node_recovery/map_visual_r01/resource_summary.json")["nodes"]
    old_session = read("codex_coding/results/glm_roundtrip/full_distributed02/session.json")
    max_dram = max(x["fixed_runtime_dram_increment_bytes"] for x in runtime.values())
    max_hbm = max(x["fixed_runtime_hbm_increment_bytes"] for x in runtime.values())
    margin, staging = 8 * MIB, 64 * MIB

    def role(name, worker, node, gpu, process="new", source_for=None):
        p = next(x for x in physical.get(node, physical["node28"])["GPU"] if x["physical_gpu"] == gpu)
        return dict(role=name, worker=worker, node=node, gpu=gpu, BDF=p["BDF"], NUMA=p["NUMA"],
                    process_accounting=process, source_for=source_for,
                    physical_mapping_evidence="static_28_30" if node in physical else "historical_template_only",
                    selected_path_functional_history=(node, gpu) in (("node28", 0), ("node30", 4)),
                    fresh_runtime_qualified=False, actual_qualified_HCA="shca_0:1" if (node, gpu) in (("node28", 0), ("node30", 4)) else None,
                    prospective_affinity_HCA={0: "shca_0", 1: "shca_1", 4: "shca_2", 5: "shca_3"}.get(p["NUMA"]),
                    prospective_affinity_HCA_runtime_validated=False)

    two = [role(f"T{i}", i, "node28", gpu) for i, gpu in enumerate((0, 3, 4, 7))]
    two += [role(f"S{i}", i + 4, "node30", gpu, source_for=i) for i, gpu in enumerate((0, 3, 4, 7))]
    three = [role("T0", 0, "node28", 0), role("T1", 1, "node28", 3),
             role("T2", 2, "node30", 4), role("T3", 3, "node30", 7),
             role("S0", 4, "node27", 0, source_for=0), role("S1", 5, "node27", 3, source_for=1),
             role("S2", 6, "node27", 4, source_for=2), role("S3", 7, "node28", 4, source_for=3)]
    shared = [role(f"T{i}/S{(i-1)%4}", i, "node28" if i < 2 else "node30", (0, 3, 4, 7)[i], source_for=(i-1)%4) for i in range(4)]

    def ledger(roles, demand, prefix):
        rows = []
        for node in sorted({r["node"] for r in roles}):
            local = [r for r in roles if r["node"] == node]
            targets = sum(r["role"].startswith("T") for r in local)
            sources = sum(r["source_for"] is not None for r in local)
            new_count = sum(r["process_accounting"] == "new" for r in local)
            obs = runtime.get(node)
            dram = obs["fixed_runtime_dram_increment_bytes"] if obs else max_dram
            hbm = obs["fixed_runtime_hbm_increment_bytes"] if obs else max_hbm
            base, cache, target = sources*demand, targets*prefix, targets*demand
            scratch = 2*demand
            projected_dram = new_count*dram + base + scratch + margin
            projected_hbm = new_count*hbm + cache + target + margin
            rows.append(dict(node=node, new_workers=new_count, existing_keeper_workers_admissible=0,
                target_roles=targets, source_roles=sources, budget_dram_bytes=2*GIB, budget_hbm_bytes=2*GIB,
                sampled_single_worker_runtime_dram_bytes=dram, sampled_single_worker_runtime_hbm_bytes=hbm,
                runtime_observation_node=node if obs else "node28/30 max extrapolation; unqualified node",
                base_dram_bytes=base, cache_hbm_bytes=cache, target_hbm_bytes=target,
                staging_hbm_bytes=new_count*staging, staging_already_inside_runtime_hbm_observation=True,
                verification_dram_scratch_provisional_bytes=scratch, dynamic_margin_bytes=margin,
                projected_new_runtime_dram_bytes=new_count*dram, projected_new_runtime_hbm_including_staging_bytes=new_count*hbm,
                conservative_projected_dram_bytes=projected_dram, conservative_projected_hbm_bytes=projected_hbm,
                projection_exceeds_dram_budget=projected_dram>2*GIB, projection_exceeds_hbm_budget=projected_hbm>2*GIB,
                multiworker_physical_peak_bytes=None, actual_incremental_runtime_bytes=None,
                physical_lower_bound=False, budget_admission_proven=False))
        return dict(demand_bytes_per_target=demand, prefix_bytes_per_target=prefix,
                    common_base_dram_bytes=4*demand, common_target_hbm_bytes=4*demand,
                    all_prefix_cache_hbm_bytes=4*prefix, rows=rows,
                    projection_scope="Planning projection only: shared mapped libraries/page cache can change increments; extra pair communicators can increase them. No multiworker physical lower bound or peak claim.")

    candidates = []
    for name, roles, equivalent, reason in (
        ("two_nodes_eight_distinct_workers", two, "logical_disjoint_pairs_preserved_hardware_conditions_unknown", "4 new runtimes/node; shared shca_0 source/target resources and contention unknown"),
        ("three_nodes_eight_distinct_workers", three, "logical_disjoint_pairs_preserved_hardware_conditions_unknown", "3+3+2 new runtimes; node27 availability and pending D cleanup remain unverified"),
        ("two_nodes_four_target_source_reused_workers", shared, "not_proven_changed_DAG", "Each worker is another target's source; operation-ready dependencies and source exports couple target completion times")):
        candidates.append(dict(candidate_id=name, ready=False, rolemap=roles, worker_count=len(roles),
            physical_GPU_count=len({(r['node'],r['gpu']) for r in roles}), equivalence=equivalent, blocker=reason,
            theoretical_literal_ledger=ledger(roles, 8*GIB, 4*GIB),
            bounded_bytes_illustration_ledger=ledger(roles, 8*MIB, 4*MIB),
            bounded_C0_full_cache_ledger=ledger(roles, 8*MIB, 8*MIB),
            bounded_bytes_illustration_status="Matches the parent interface agreement: 4 ranks x 2 blocks x 4MiB, common base32MiB; C1/C2 cache cap16MiB, C0 cap32MiB. Existing 2x16MiB manifests, TP4 physical binding and service ratios remain unverified.",
            mesh_pair_group_count=len(roles)*(len(roles)-1)//2,
            mesh_runtime_increment_for_this_group_count_measured=False, full_physical_peak_verified=False,
            launch_command=None, GPU_execution_authorized=False))

    metadata = {}
    for node, d in new_guards.items():
        before = old_guards[node]
        protected_before = {x["id"]:x["State"]["StartedAt"] for x in before["protected_containers"]}
        protected_after = {x["id"]:x["State"]["StartedAt"] for x in d["protected_containers"]}
        metadata[node] = dict(sampled_at_utc=d["sampled_at_utc"], container_identity_checks=d["container_identity_checks"],
            boot_matches_previous=d["boot_id"]==before["boot_id"], protected_container_identities_match=protected_before==protected_after,
            exact_old_process_identities_preserved=all(x["owner_matches"] for x in d["keeper_process_discovery"]),
            exact_old_process_count=len(d["keeper_process_discovery"]),
            source_assets_match=all(x["sha256"]==x["expected_sha256"] for x in d["source_assets"].values()),
            source_assets_scope="new isolated project files; not loaded old keeper Python code",
            pids_current=int(d["container_cgroup"]["files"]["pids.current"]),
            container_cgroup_memory_max_bytes=int(d["container_cgroup"]["files"]["memory.max"]),
            task_dram_quota_bytes=2*GIB, task_hbm_quota_bytes=2*GIB,
            cgroup_ceiling_is_not_task_authorization=True, keeper_rpc_calls=0,
            fresh_catalog_integrity_verified=False, fresh_full_model_content_verified=False,
            fresh_gpu_runtime_qualified=False, refreshed_ports=False, full_status="unknown",
            ref=ref(out/("metadata_"+node)/"stdout.json"))

    old_keeper_rows=[]
    for x in old_guards["node28"]["keepers"]:
        b=x["body"]
        old_keeper_rows.append({k:b[k] for k in ("worker","node","gpu","numa","epoch","session","pid","used_bytes","staging_bytes")}|dict(endpoint=x["endpoint"], catalog_sha256=x["catalog_sha256"], replica_count=len(b["replicas"])))
    evidence_paths = [ROOT/"AGENTS.md", ROOT/"docx/next_step.txt", ROOT/"docx/debug_philosophy.md",
        ROOT/"docx/articles/2026-10-10_joint_planning_necessity_and_boundaries.md",
        ROOT/"docx/experiments/2026-10-04_v0.1.23.3_task2_paper_e1_e8_records.md",
        ROOT/"docx/plans/2026-10-04_v0.1.23.3_task2_paper_e1_e8_plan.md",
        RESULT/"layout_validation/execution_r01/execution.json", RESULT/"layout_validation/review_final_r02/qualification.json",
        RESULT/"decision_validation/measure_refresh_r01/summary.json",
        RESULT/"three_node_recovery/map_visual_r01/resource_summary.json",
        ROOT/"codex_coding/results/glm_roundtrip/full_distributed02/session.json",
        ROOT/"main_lib/fluidckpt/cluster/worker.py",ROOT/"main_lib/fluidckpt/cluster/control.py",
        ROOT/"main_lib/fluidckpt/cluster/service_cost.py", ROOT/"main_lib/fluidckpt/executor/chunks.py",
        ROOT/"main_lib/fluidckpt/serving/keeper.py",Path(__file__),Path(__file__).with_name("metadata_refresh.py")]
    result=dict(schema="fluidckpt.tp4_deployment_feasibility.v1", development="v0.1.23.3/task2",
        created_utc=datetime.now(timezone.utc).isoformat(), ready=False, status="blocked_pending_case_and_budget_admission",
        GPU_execution_authorized=False, GPU_calls=0, new_GPU_workers=0, remote_writes=0, signals=[], SSH_metadata_calls=2,
        process_architecture=dict(theoretical_fixture_workers=8, distinct_target_workers=4, distinct_source_workers=4,
            eight_workers_mathematically_necessary=False,
            eight_workers_necessary_for_unmodified_fixture_rolemap=True,
            four_independent_target_ready_states_required_for_fixture=True,
            fewer_sources_or_target_as_source_requires_new_DAG_and_service_validation=True,
            CPU_owner_is_not_standalone_Cluster_transfer_source=True,
            minimum_under_single_worker_projection=dict(new_runtime_capacity_per_node=1, distinct_worker_nodes=8,
                status="Conditional bookkeeping option only; no eight-node allocation or actual physical minimum proved")),
        runtime_evidence=dict(single_worker_observations=runtime, staging_bytes_per_worker=staging,
            runtime_hbm_already_includes_staging=True, runtime_dram_can_share_pages=True,
            two_worker_pair_groups=1, eight_worker_pair_groups=28, actual_multiworker_runtime_increment=None,
            historical_observed_dram_peak_increment_bytes={"node28":2118258688,"node30":2089222144},
            full_physical_peak_verified=False, margin_is_not_proven_sufficient=True),
        metadata=metadata, hardware=dict(nodes_28_30_homogeneous=True, static_GPU_NUMA=[0,3,2,1,4,7,6,5],
            static_HCA_NUMA=[0,1,4,5], static_HCA_by_NUMA={"0":"shca_0","1":"shca_1","4":"shca_2","5":"shca_3"},
            latest_product_path=dict(node28_GPU=0,node30_GPU=4,HCA="shca_0:1",network="IBext_v8",gdr=0),
            HCA_independence_measured=False, other_GPU_current_functional_admission=False,
            rate_matrix=None, concurrent_four_pair_capacity=None, actual_staging_page_NUMA=None),
        keeper_reuse=dict(admissible=False, admitted_reusable_workers=0, new_keeper_rpc_calls=0,
            prior_catalog_timestamp_utc=old_guards['node28']['started_utc'], retained_cluster_workers=old_keeper_rows,
            retained_cluster_mesh=old_session['args']['mesh'], retained_session=old_session['args']['run'],
            historical_development=old_session['args']['development'], loaded_old_product_code_sha256=None,
            current_disk_source_SHA_proves_loaded_code=False, protecting_full_model_content=True,
            prior_catalog_base_dram_unique_bytes=sum(x['used_bytes']['dram'] for x in old_keeper_rows),
            physical_shared_memfd_must_not_sum_RSS=True,
            budget_accounting="Existing protected runtime and model base stay in the baseline. They are not free workers or the new fixture base. No CPU/GPU borrowing occurred.",
            rejection_reasons=["Cluster.inventory requires worker ranks 0..N-1 and one common session; node28 retained workers are ranks16..21 in full_distributed02",
                "Existing22-rank communicator includes node27/29; no reset/rejoin/remap is authorized or proven",
                "39702..39707 processes are serving.keeper rather than ClusterWorker; cluster catalog/prepare/execute compatibility is unproven",
                "Old loaded source version, TP4 content identity, full cluster health and clean idle state are not freshly certified",
                "Preparing/transferring would acquire leases and consume protected model runtime resources; metadata visibility alone grants no reuse"]),
        candidates=candidates, minimal_next_evidence=[
            "Freeze exact TP4 content intervals, common base, source-target DAG and identical candidate opportunities",
            "Keep the 2GiB/node task limit; either certify incremental runtime under this limit or identify a separately reviewable resource allocation, without applying it",
            "Validate changed DAG before proposing fewer workers as equivalent; qualify selected GPUs and actual HCA resources only after case and budget admission",
            "Measure one same-primitive path and independent concurrency/holdout only after admission; do not convert static HCA link speed into service capacity"],
        pending_preserved=dict(node27_collector_pid=4037472,node27_collector_start_ticks="477235183",node27_exit_verified=False,
            node29_processes=[dict(pid=21883,start_ticks="475875814"),dict(pid=21901,start_ticks="475875846")],node29_exit_verified=False,
            node27_driver_accessed=False,node29_accessed=False), evidence=[ref(x) for x in evidence_paths])
    (out/"deployment_feasibility.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    lines=["# TP4 部署可行性与预算账本", "", "开发编号 v0.1.23.3/task2。ready=false；本轮只读元数据与本地CPU核算，没有启动GPU或申请扩大配额。", "",
        "原CPU平台期fixture是4个target+4个source、8个不同worker。8不是TP4的数学必要数量，但它保留了原例的四个互不相交worker pair。把target兼作其他target的source会加入source-ready依赖；合并source会串行其出口，不能沿用Ti=8−3xi的独立服务式。CPU owner也不是当前Cluster.transfer的独立GPU传输端。", "",
        "预算保持DRAM/HBM各2GiB/node；隔离容器8GiB cgroup上限不是任务授权。两端单worker warm后DRAM观测为2,029,518,848/2,038,231,040B，HBM为371,302,400/371,310,592B。HBM观测已含64MiB staging，下面另列staging但不重复加入。独立worker观测线性相加只是保守预算投影：共享映射库/页缓存可降低增量，8worker mesh的28个pair group又可能增加开销；真实多worker峰值和最小物理内存仍unknown。", "",
        "字节账本按主agent接口约定：每rank两个4MiB块、d=8MiB，共4rank/32MiB完整DRAM底本，8个local candidate各4MiB。C1/C2总cache上限16MiB，下表以每target保存一个4MiB块的对称分配示例计账；C0总cache上限32MiB，JSON另列每target两块全保活账本。原2×16MiB manifest并不自动变成真实TP4。各节点预留2×d校验DRAM临时区和8MiB余量，仅用于投影；前轮8MiB margin已未覆盖全部增长，不能认证峰值。原理论8GiB/4GiB参数的底本/target/cache分别32/32/16GiB，单target也不满足当前2GiB/node任务HBM额度。", "",
        "| 候选 | worker分布 | 节点 | runtime DRAM投影B | 底本B | cache/target/staging HBM B | 含应用DRAM投影B | 2GiB DRAM投影 |", "|---|---|---|---:|---:|---|---:|---|"]
    for c in candidates:
        for row in c['bounded_bytes_illustration_ledger']['rows']:
            lines.append(f"| {c['candidate_id']} | {len(c['rolemap'])}总worker | {row['node']}({row['new_workers']}) | {row['projected_new_runtime_dram_bytes']:,} | {row['base_dram_bytes']:,} | {row['cache_hbm_bytes']:,}/{row['target_hbm_bytes']:,}/{row['staging_hbm_bytes']:,} | {row['conservative_projected_dram_bytes']:,} | {'超投影、未准入' if row['projection_exceeds_dram_budget'] else '仅投影内、未准入'} |")
    lines += ["", "两节点8worker是4+4；三节点8worker可3+3+2，但27仍有旧D采集进程退出未证，未访问其驱动，也未访问29。仅按单worker保守投影、若每node放1个新worker，需要8个节点；这是条件式账本选项，不是已证物理最小配置，也没有获得8节点授权。当前两/三节点方案均未证明预算准入。", "",
        "28/30静态各8GPU、8NUMA、4HCA，GPU NUMA编号0/3/2/1/4/7/6/5；HCA位于NUMA0/1/4/5。rolemap列出GPU0/3/4/7与这些位置的亲和建议，但当前产品资格只覆盖28GPU0↔30GPU4，经双方shca_0:1、IBext_v8、gdr0。HCA亲和性不是独立容量实测；单node多source/target使用shca_0时共享同资源ID，不能为每GPU复制一份网卡带宽。", "",
        "旧keeper不能合法直接复用。28保留cluster workers的session=full_distributed02、ranks16–21，原mesh是node27:0-7,node29:0-7,node28:2-7，共22rank；当前控制器要求连续rank和单session，现有pair communicator不能跨新session拼接。旧39702–39707为serving.keeper，未证明支持cluster prepare/execute。历史启动development=v0.1.23.2，仅是历史来源标签；旧进程当前加载的源代码SHA未知，磁盘新源码SHA不能证明热进程版本。保留GLM DRAM目录合计294,078,259,200B是受保护底本，不能替换为本轮32MiB fixture或当空余容量。", "",
        "本轮08:59:04UTC各一次metadata-only刷新通过：同boot/CID/StartedAt，隔离容器pids1/noOOM，28两个旧容器和13个已知进程identity保持，新隔离project资产SHA匹配。复用已部署guard但在内存中将keeper endpoints置空，禁pycache；没有keeper RPC、HIP/SMI、信号或远端写。目录完整性仍引用07:48旧证据，本轮未升级full status，也未刷新端口/模型内容/新运行时健康。", "",
        "下一步先冻结case的真实内容与DAG，然后证明当前额度可准入或形成独立可审资源方案。新process与existing protected keeper分别计账；无需重造worker。缩块只能降低应用字节，不能证明固定runtime可叠加。共享HCA容量、路径成本与并发执行都留unknown，launch_command=null。", "", "机器可读原件：deployment_feasibility.json；metadata_node28/30保存原stdout、stderr、command与时间戳。所有证据SHA见JSON evidence。"]
    (out/"deployment_feasibility.md").write_text("\n".join(lines)+"\n")
    checks = dict(no_ready=not result['ready'], no_launch=all(c['launch_command'] is None for c in candidates),
        metadata_no_RPC=all(d['keeper_rpc_calls']==0 for d in new_guards.values()),
        metadata_identity=all(all(d['container_identity_checks'].values()) for d in new_guards.values()),
        metadata_no_signals=all(not d['signals'] for d in new_guards.values()),
        runtime_staging_not_double_counted=all(r['conservative_projected_hbm_bytes']==r['projected_new_runtime_hbm_including_staging_bytes']+r['cache_hbm_bytes']+r['target_hbm_bytes']+r['dynamic_margin_bytes'] for c in candidates for r in c['bounded_bytes_illustration_ledger']['rows']),
        totals_preserved=all(c['bounded_bytes_illustration_ledger']['common_base_dram_bytes']==32*MIB for c in candidates),
        quota_preserved=all(r['budget_dram_bytes']==2*GIB and r['budget_hbm_bytes']==2*GIB for c in candidates for r in c['bounded_bytes_illustration_ledger']['rows']),
        no_physical_lower_bound=all(not r['physical_lower_bound'] for c in candidates for r in c['bounded_bytes_illustration_ledger']['rows']),
        protected_keepers_not_admitted=result['keeper_reuse']['admitted_reusable_workers']==0)
    (out/"validation.json").write_text(json.dumps(dict(passed=all(checks.values()),checks=checks, source=ref(Path(__file__)), artifacts=[ref(out/'deployment_feasibility.json'),ref(out/'deployment_feasibility.md')]),ensure_ascii=False,indent=2)+"\n")
    if not all(checks.values()): raise ValueError(checks)
    print(json.dumps(dict(out=str(out),ready=False,validation=checks),ensure_ascii=False))


if __name__ == "__main__":
    main()
