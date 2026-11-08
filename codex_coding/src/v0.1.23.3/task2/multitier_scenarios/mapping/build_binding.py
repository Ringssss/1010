"""Bind CPU scenario roles to evidenced static hosts without runtime claims."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[6]
BASE = ROOT / "codex_coding/results/v0.1.23.3/task2/multitier_scenarios"

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    out = BASE / "mapping_binding_r03"
    out.mkdir(exist_ok=False)
    refs = []
    hosts, pci = {}, {}
    for node in ("node27", "node28", "node30"):
        for folder, target in (("mapping_refresh_r01", hosts), ("mapping_pci_followup_r01", pci)):
            path = BASE / folder / (node + ".stdout.json")
            target[node] = json.loads(path.read_text())
            refs.append({"path": str(path.relative_to(ROOT)), "sha256": sha(path)})
        assert hosts[node]["boot_id"] == pci[node]["boot_id"]
        assert len(pci[node]["gpus"]) == 8
    old27 = ROOT / "codex_coding/results/v0.1.23.3/task2/resource_map/provenance_r01/node27/host.json"
    old = json.loads(old27.read_text())
    refs.append({"path": str(old27.relative_to(ROOT)), "sha256": sha(old27)})
    roles = [("N0", "node28", [("g0", 0, "rank0_target"), ("g1", 1, "helper0")], "/data/node28"),
             ("N1", "node30", [("g0", 4, "rank1_target"), ("g1", 5, "helper1")], "/data/node30"),
             ("N2", "node27", [("g0", 0, "rank2_target"), ("g1", 4, "rank3_target")], "/home")]
    ordinal_bdf = ["0000:09:00.0", "0000:36:00.0", "0000:55:00.0", "0000:77:00.0", "0000:85:00.0", "0000:b5:00.0", "0000:d5:00.0", "0000:f5:00.0"]
    owners = {}
    for node in ("node28", "node30"):
        path = BASE / "mapping_owner_refresh_r01" / (node + ".stdout.json")
        owners[node] = json.loads(path.read_text())
        assert owners[node]["boot_id"] == hosts[node]["boot_id"]
        refs.append({"path": str(path.relative_to(ROOT)), "sha256": sha(path)})
    run27_path = BASE / "mapping_admission27_run_r01/artifacts/child.result.json"
    run27 = json.loads(run27_path.read_text())
    assert run27["status"] == "passed" and run27["actual_bdf"] == ordinal_bdf[0]
    refs.append({"path": str(run27_path.relative_to(ROOT)), "sha256": sha(run27_path)})
    binding = {"schema_version": "fluidckpt.multitier.static_machine_binding.v1",
               "status": "static_binding_candidate_runtime_unadmitted", "physical_ready": False,
               "development": "v0.1.23.3/task2", "created_utc": datetime.now(timezone.utc).isoformat(),
               "logical_contract_hash": "741def7882e9163fb6afd299c27c1bff9c0207529d9d555c754d66f5492111fe",
               "hosts": [], "evidence_refs": refs, "node29_accesses": 0,
               "node29_pending": {"historical_only": True, "pids": [21883, 21901], "new_exit_evidence": False},
               "node27_old_pending": {**hosts["node27"]["pending_exact"][0],
                                       "old_boot_id": old["boot_id"], "new_boot_id": hosts["node27"]["boot_id"],
                                       "boot_changed": old["boot_id"] != hosts["node27"]["boot_id"],
                                       "old_same_boot_D_process_premise_cleared": True,
                                       "new_gpu_health_verified": "single GPU0 initcopy passed; other GPUs unverified"},
               "task_budget": {"legacy_cap_bytes_per_node": 2 << 30,
                               "legacy_origin": "old small-experiment provisional gate, not a user hardware maximum",
                               "legacy_cap_applied_or_expanded_by_this_collector": False,
                               "next_proposed_dram_cap_per_node_bytes": 6 << 30,
                               "next_proposed_hbm_cap_per_node_bytes": 2 << 30,
                               "new_cap_is_runtime_measurement": False},
               "rolemap_delta_from_r02": {"N0_helper": "GPU4/NUMA4 to GPU1/NUMA3 to avoid known keeper", "N1_helper": "GPU0/NUMA0 to GPU5/NUMA7 based on focused free/owner metadata"},
               "rates": None, "schedule_qualified": False, "model_binding": None,
               "gpu_calls": 0, "remote_writes": 0, "signals": []}
    for logical, node, workers, path in roles:
        h = hosts[node]
        by_bdf = {g["bdf"]: g for g in pci[node]["gpus"]}
        s = next(s for s in h["storage_paths"] if s["path"] == path)
        mount = s["mount"]
        block = next(b for b in h["block_devices"] if "/dev/" + b["name"] == mount["source"])
        physical_disk_name = re.sub(r"p[0-9]+$", "", block["name"])
        physical = next(b for b in h["block_devices"] if b["name"] == physical_disk_name)
        pci_parts = [x for x in Path(physical["resolved"]).parts if re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]", x)]
        row = {"logical_node": logical, "node": node, "hostname": h["hostname"], "boot_id": h["boot_id"],
               "metadata_sampled_utc": h["started_utc"], "pci_sampled_utc": pci[node]["sampled_utc"],
               "roles": [], "dram_positions": [{"logical": "D0", "numa": int(by_bdf[ordinal_bdf[workers[0][1]]]["pci_chain"][0]["numa_node"])},
                                                {"logical": "D1", "numa": int(by_bdf[ordinal_bdf[workers[1][1]]]["pci_chain"][0]["numa_node"])}],
               "host_memavailable_bytes": int(next(x.split()[1] for x in h["host_memory"].splitlines() if x.startswith("MemAvailable:"))) * 1024,
               "ssd": {"logical": "S" + logical[1:], "verified_existing_parent": path, "mount": mount,
                       "physical_disk": physical_disk_name, "medium": "local_nvme_nonrotational",
                       "model": physical["model"], "rotational": physical["rotational"],
                       "device_size_bytes": int(physical["size_512b_sectors"]) * 512,
                       "device_bdf_chain_from_resolved_sysfs": list(reversed(pci_parts)),
                       "mount_available_bytes": s["statvfs"]["available_bytes"],
                       "proposed_owned_directory": path + "/zhujianian/fluidckpt_multitier_r01",
                       "owned_directory_exists": None, "ownership_writable_verified": False,
                       "io_qualified": False, "page_cache_state": "unknown", "ssd_numa": None,
                       "file_manifest_committed": False, "service_started": False},
               "hca_opportunities": h["hca"], "runtime_admitted": False, "hbm_free_bytes": None,
               "container_capacity_is_task_authorization": False,
               "selected_gpu_owner_state": "unknown; process visibility cannot prove GPU unused",
               "selected_gpu_reassignment_requires_rolemap_delta": True}
        for lg, ordinal, role in workers:
            bdf = ordinal_bdf[ordinal]
            g = by_bdf[bdf]
            numa = int(g["pci_chain"][0]["numa_node"])
            distance = [int(x) for x in h["numa"]["node" + str(numa)]["distance"].split()]
            hca_distances = {hca["name"]: distance[int(hca["pci_chain"][0]["numa_node"])] for hca in h["hca"]}
            near = min(hca_distances, key=lambda name: (hca_distances[name], name))
            free = next((r for r in owners.get(node, {}).get("gpus", []) if r["bdf"] == bdf), None)
            row["roles"].append({"logical_gpu": lg, "role": role, "historical_ordinal": ordinal,
                                  "ordinal_binding_current_HIP_verified": False, "bdf": bdf, "numa": numa,
                                  "pci_chain": g["pci_chain"], "near_hca": near,
                                  "near_hca_numa_distance": hca_distances[near], "hca_numa_distances": hca_distances,
                                  "shared_shca0_numa_distance": distance[0], "focused_owner_free_metadata": free,
                                  "runtime_qualified": bool(node == "node27" and ordinal == 0),
                                  "runtime_scope": "fresh1MiB copy only" if node == "node27" and ordinal == 0 else "unadmitted"})
        binding["hosts"].append(row)
    binding["topology_choices"] = {
        "shared_shca0": "Selected roles have shca_0 distances: N0=10/15, N1=34/28, N2=10/34",
        "near_hca": "N0 GPUs0/1 both nearest shca_0; N1 GPUs4/5 both nearest shca_2; N2 GPU0 nearest shca_0 and GPU4 nearest shca_2",
        "all_baselines_same_HCA_opportunities": True,
        "independence_or_capacity_measured": False,
        "historically_qualified_subset": "node28 GPU0/BDF09 to node30 GPU4/BDF85 via shca_0:1/IBext_v8/gdr0"}
    binding["unknowns_and_blockers"] = [
        "Node27 GPU0 fresh1MiB initcopy passed; other five selected cards and six-worker aggregate still need bounded runtime gate",
        "New six-worker mesh, same-host GPU paths and shca_2 paths are not qualified",
        "DRAM6GiB/node and HBM2GiB/node caps need aggregate per-phase measurement before six-worker admission",
        "Local NVMe parent mounts are bound; task-owned writable directories and SSD service/data manifests remain uncreated",
        "SSD HTTP proxy placement, buffers/pagecache NUMA accounting and actual disk IO/cache state are unverified",
        "Shared PCI/HCA effective capacity, restore finite schedule and actual processed TP4 model are unbound"]
    (out / "machine_binding.json").write_text(json.dumps(binding, ensure_ascii=False, indent=2) + "\n")
    (out / "build_binding.source.py").write_bytes(Path(__file__).read_bytes())
    lines = ["# 三GPU节点六角色静态绑定候选", "", "仅静态metadata资格，physical_ready=false。10:41–10:43 UTC有界读取27/28/30均自然rc0；node29未访问。",
             "", "node27 old4037472/start477235183已absent，boot由" + old["boot_id"] + "变为" + hosts["node27"]["boot_id"] + "，跨重启边界旧D身份不再成立；没有恢复退出码，不猜重启原因。随后主执行新GPU0 1MiB initcopy自然rc0/SHA通过，仅此卡功能资格。",
             "", "|逻辑|实机|目标/helper物理卡|DRAM NUMA|SSD真实mount|available B|", "|---|---|---|---|---|---:|"]
    for r in binding["hosts"]:
        lines.append("|" + "|".join([r["logical_node"], r["node"], ";".join(x["role"] + " GPU" + str(x["historical_ordinal"]) for x in r["roles"]), "/".join(str(p["numa"]) for p in r["dram_positions"]), r["ssd"]["verified_existing_parent"] + "→" + r["ssd"]["mount"]["source"], str(r["ssd"]["mount_available_bytes"])]) + "|")
    lines += ["", "D0/D1按逻辑g0/g1顺序映射。r01顺序错误与r02原卡候选保留；r03按主协调把28helper从GPU4换GPU1、30helper从GPU0换GPU5。28 GPU4有确证29700/39700旧keeper；新28 GPU0/1和30 GPU4/5获得10:56 UTC低占用/可见owner数值证据，仍非exclusive或HIP功能资格。",
              "", "共同shca0的NUMA距离N0=10/15、N1=34/28、N2=10/34。近HCA分别N0共同shca0、N1共同shca2、N2拆shca0/2。保留有意义近远和共同出口组合；静态距离不是带宽或独立通路实测。全部baseline必须拥有相同合法HCA机会。",
              "", "首轮class03选择误列了板载VGA，raw保留mapping_refresh_r01；专门mapping_pci_followup_r01校正为Hygon PCI class0b4000/vendor1d94/device6320，各node8卡。不要使用首轮gpus字段作为DCU图。",
              "", "blockdev容量由sysfs size×512，df容量由本地mount statvfs取得，未执行blockdev/df CLI。NVMe实际diskBDF04，PCI父链来自resolved sysfs；SSD NUMA仍unknown。网络/autofs路径未stat，不把共享checkpoint命名为节点本地SSD。",
              "", "2GiB来自旧小实验暂定gate，不是用户硬上限；本collector无配额变更。主协调的新6GiB DRAM/2GiB HBM每node有限cap须以聚合实测验收。",
              "", "最小下一步：补其余五卡同scope资格与六worker聚合预算、SSD own目录和同原语串行功能。27GPU0已通过1MiB复制，不重复运行它来增加样本。", "", "未知/阻塞："] + ["- " + s for s in binding["unknowns_and_blockers"]]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"out": str(out), "status": binding["status"], "gpu_calls": 0}))

if __name__ == "__main__":
    main()
