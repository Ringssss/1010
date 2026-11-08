"""Audit the mapping delivery and the parent's one node27 functional run."""
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
TASK = ROOT / "codex_coding/results/v0.1.23.3/task2"
BASE = TASK / "multitier_scenarios"

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    out = BASE / "mapping_final_r01"
    out.mkdir(exist_ok=False)
    refs, checks = [], {}
    def load(path):
        refs.append({"path": str(path.relative_to(ROOT)), "sha256": sha(path)})
        return json.loads(path.read_text())
    binding = load(BASE / "mapping_binding_r03/machine_binding.json")
    manifests = [load(BASE / folder / "manifest.json") for folder in
                 ("mapping_refresh_r01", "mapping_pci_followup_r01", "mapping_owner_refresh_r01")]
    checks["all_metadata_commands_natural_rc0"] = all(r["returncode"] == 0 for m in manifests for r in m["commands"])
    checks["all_metadata_known_hosts_unchanged"] = all(m["known_hosts_unchanged"] for m in manifests)
    checks["node29_never_accessed"] = all(m["node29_accesses"] == 0 for m in manifests)
    checks["no_node27_driver_refresh"] = set(r["node"] for r in manifests[-1]["commands"]) == {"node28", "node30"}
    checks["six_roles_three_real_gpu_hosts"] = len(binding["hosts"]) == 3 and sum(len(h["roles"]) for h in binding["hosts"]) == 6
    checks["dram_order_matches_gpu_numa"] = all([d["numa"] for d in h["dram_positions"]] == [r["numa"] for r in h["roles"]] for h in binding["hosts"])
    checks["r03_rolemap_matches_parent_choice"] = [[r["historical_ordinal"] for r in h["roles"]] for h in binding["hosts"]] == [[0, 1], [4, 5], [0, 4]]
    checks["static_binding_not_six_worker_runtime"] = binding["physical_ready"] is False
    checks["local_ssds_are_real_nonrotational_mounts"] = all(h["ssd"]["mount"]["fstype"] == "ext4" and h["ssd"]["rotational"] == "0" for h in binding["hosts"])
    owner_evidence = []
    for node in ("node28", "node30"):
        old = load(TASK / "three_node_recovery/inventory_r03" / (node + ".stdout.json"))
        current = load(BASE / "mapping_owner_refresh_r01" / (node + ".stdout.json"))
        checks[node + "/historical_kfd_id_same_boot"] = old["boot_id"] == current["boot_id"]
        ids = {g["pci_chain"][0]["bdf"]: g["gpu_id"] for g in old["kfd"]}
        for gpu in current["gpus"]:
            key = "vram_" + ids[gpu["bdf"]]
            active = [p["pid"] for p in current["kfd_processes"] if int(p["kfd_numeric_files"].get(key, "0")) > 0]
            row = {**gpu, "node": node, "sampled_utc": current["sampled_utc"],
                   "historical_kfd_gpu_id": ids[gpu["bdf"]], "same_boot_mapping": old["boot_id"] == current["boot_id"],
                   "visible_positive_vram_owner_pids": active, "exclusive_or_gpu_health_claim": False,
                   "owner_visibility_complete": False}
            owner_evidence.append(row)
            checks[node + "/" + gpu["bdf"] + "/visible_positive_vram_owners_zero"] = not active
    run = load(BASE / "mapping_admission27_run_r01/result.json")
    child = load(BASE / "mapping_admission27_run_r01/artifacts/child.result.json")
    process = load(BASE / "mapping_admission27_run_r01/artifacts/child.process.json")
    registration = load(BASE / "mapping_admission27_run_r01/artifacts/registration.json")
    checks["node27_one_copy_passed"] = run["passed"] and child["status"] == "passed" and child["correctness"]["verified_bytes"] == 1 << 20
    checks["node27_bdf_ordinal_confirmed"] = child["actual_bdf"] == "0000:09:00.0" and child["requested_gpu"] == 0
    checks["node27_clean_natural_exit"] = process["returncode"] == 0 and not process["timed_out"] and process["cleanup"]["passed"] and not process["cleanup"]["signals"] and not process["cleanup"]["pending_group_members"]
    checks["node27_core0"] = registration["core_dump_limit"] == [0, 0]
    checks["node27_fresh_identity_protection_no_oom_quiet"] = all(run[k] for k in ("protected_exact_unchanged", "fresh_container_identity_preserved", "no_new_oom", "fresh_container_quiet_after"))
    bound = child["hip_total_bytes"] - child["hip_free_bytes"] + child["payload_bytes"]
    checks["selected_node27_gpu_conservative_point_bound_under_2GiB"] = bound <= 2 << 30
    checks["node27_runtime_source_exact_frozen_diag60"] = registration["source_sha256"] == sha(ROOT / "codex_coding/src/v0.1.23.3/task2/three_node_recovery/diagnosis30/init_gate_diag60.py")
    for path in Path(__file__).parent.glob("*.py"):
        ast.parse(path.read_text())
        refs.append({"path": str(path.relative_to(ROOT)), "sha256": sha(path)})
    checks["all_mapping_source_AST_passed"] = True
    plan_path = ROOT / "docx/plans/2026-10-04_v0.1.23.3_task2_paper_e1_e8_plan.md"
    text = plan_path.read_text()
    quote = next(line for line in text.splitlines() if line.startswith("暂定新增物理实验配额每node"))
    (out / "budget_origin.snapshot.md").write_text(quote + "\n")
    budget = {"source_path": str(plan_path.relative_to(ROOT)), "source_document_sha256_at_read": sha(plan_path),
              "source_section": "18.18", "source_line_at_read": 660,
              "exact_old_budget_quote": quote, "interpretation": "old experiment provisional cap; no evidence of permanent user hardware upper bound",
              "old_outcomes_preserved": True, "new_parent_coordinated_dram_cap_bytes": 6 << 30,
              "new_parent_coordinated_hbm_cap_bytes": 2 << 30, "new_cap_is_paused_weight_cache_budget": False}
    report = {"development": "v0.1.23.3/task2", "created_utc": datetime.now(timezone.utc).isoformat(),
              "checks": checks, "checks_passed": sum(checks.values()), "checks_total": len(checks),
              "status": "mapping_prepared_one_third_host_gpu_functional_gate_passed_six_worker_unadmitted",
              "binding_path": "mapping_binding_r03/machine_binding.json", "owner_evidence": owner_evidence,
              "budget_provenance": budget, "node27_selected_card_bound_bytes": bound,
              "node27_bound_scope": "total HIP card used at post-init sample plus one payload; includes any external allocations; no full peak or other GPU bound",
              "six_worker_ready": False, "SSD_IO_ready": False, "rates_or_schedule_qualified": False,
              "metadata_subagent_GPU_runs": 0, "parent_GPU_runs": 1, "node29_accesses": 0,
              "evidence_refs": refs,
              "known_failures_and_revisions": ["first metadata class03 selected management VGA; dedicated PCI follow-up corrected to0b4000/1d94:6320", "r01 binding DRAM order corrected r02; r03 avoids occupied node28 helperGPU4 and moves node30 helperGPU0 to low-occupancyGPU5", "local historical inventory_r01 parse failed because old stdout is empty; inventory_r03 used with same-boot evidence"]}
    assert all(checks.values()), checks
    (out / "qualification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    lines = ["# 物理绑定与第三GPU节点最小资格交付", "", "最终绑定为mapping_binding_r03：N0=28 GPU0/1与NUMA0/3，N1=30 GPU4/5与NUMA4/7，N2=27 GPU0/4与NUMA0/4。三node都是真GPU宿主，各SSD父目录均local ext4 NVMe；task own目录/服务/完整SSD文件未创建。六worker/SSD恢复/真实模型尚未准入。",
             "", "27旧4037472/start477235183 absent且boot已变，旧D进程前提解除，不恢复退出码/猜重启原因。主创建新6GiB私有IPC容器并执行一次GPU0/BDF09 initcopy：全1MiB SHA631b84027d6b9e52b539c4e8373622d23032dfadc64d60af87339c9037e4f769，通过，内运行19.990555071s，自然rc0/child13组空/no signal/noOOM/原停止容器状态保持。仅selectedGPU0单卡功能资格。",
             "", "该卡HIP total-free+1MiB=" + str(bound) + "B<2GiB，是初始化后时点含外部占用的保守selected卡上界，非task新增HBM/全生命周期峰值/同node第二卡。DRAM容器6GiB硬上限通过，memory.peak与全阶段峰值仍unknown。",
             "", "28/30 focused owner/free10:56UTC：28 GPU0/1各used584761344B/free68117938176B；30 GPU4/5各used3751936B/free68698947584B。同boot历史KFD-id对应的可见positive-vram owners为0；不证明exclusive、GPU健康或新worker可运行。GPUvisibility environ受权限限制，未知明确保留。28旧keepers在2–7，GPU4候选因此撤换；不借旧keeper或动其他服务。",
             "", "共同shca0静态NUMA距离N0=10/15、N1=34/28、N2=10/34；近HCA是N0共同0、N1共同2、N2拆0/2，含共享出口与跨NUMA机会，速率/独立性仍unknown。全部baseline必须拥有相同合法HCA机会。",
             "", "2GiB来源为总计划18.18行660旧小实验‘暂定新增物理实验配额’，不是永久用户硬上限；本轮主协调6GiB DRAM/2GiB HBM每node，且不同于暂停快层配额。旧失败和原件保持。来源完整原句保存budget_origin.snapshot.md。",
             "", "所有metadata走冻结独立Python源stdin，严格SSH、35/25s自退出deadline、无远端子进程/写/信号；node29零访问、27零driver补采。主执行setup/copy另有清晰授权和原件，collector自身GPU运行0。首次class03误选板载VGAraw保留，专门PCI补采纠正，各node真实8枚DCU。", "", "产物与复现命令：", "", "- mapping_refresh_r01：collect_metadata.py --out .../mapping_refresh_r01（3node静态/容器/mount）",
             "- mapping_pci_followup_r01：collect_metadata.py --host-source pci_metadata.py --out .../mapping_pci_followup_r01",
             "- mapping_owner_refresh_r01：collect_metadata.py --host-source owner_metadata.py --nodes node28 node30 --out .../mapping_owner_refresh_r01",
             "- mapping_binding_r01/r02/r03：build_binding.py，最终r03，前两原件保留",
             "- mapping_admission27_plan_r01/r02：prepare_admission27.py；最终r02 source refs冻结",
             "- mapping_admission27_setup_r01与mapping_admission27_run_r01：主执行setup、run_admission27_copy.py --plan ...plan_r02 --setup ...setup_r01/stdout.json --out ...run_r01 --execute；source SHA及完整argv在原件",
             "- mapping_final_r01：summarize_delivery.py；" + str(sum(checks.values())) + "项本agent离线审计通过，独立review由主与reviewer另交。", "", "下一步只补余下五卡scope及六worker聚合预算/同node路径，然后task own SSD目录和同原语串行恢复；27GPU0成功观察不重复增加样本。共享XML/defaultmapping/旧容器不改。"]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"out": str(out), "checks_passed": sum(checks.values()), "checks_total": len(checks), "selected_gpu_point_bound_bytes": bound}))

if __name__ == "__main__":
    main()
