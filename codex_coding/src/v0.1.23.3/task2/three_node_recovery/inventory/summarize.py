"""Summarize frozen inventory without any host or GPU calls."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import re

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    nodes={}
    for node in ("node28","node30"):
        path=args.input/(node+".stdout.json")
        d=json.loads(path.read_text())
        names={c["id"]:c["name"] for c in d["containers"]}
        def owner(p):
            cg=p["cgroup"]["proc_cgroup"]
            match=re.search(r"docker-([0-9a-f]{64})\.scope",cg) if isinstance(cg,str) else None
            name=names.get(match[1]) if match else None
            return {"pid":p["pid"],"start_time_ticks":p.get("start_time_ticks"),"state":p.get("state"),"comm":p.get("comm"),"container":name,"cgroup":cg,"ownership":"project_legacy_candidate" if name and name.startswith("zjn-") else "external_or_unknown", "vram_by_gpu_id":{k[5:]:int(v) for k,v in p["kfd_files"].items() if re.fullmatch(r"vram_[0-9]+",k) and isinstance(v,str)}}
        owners=[owner(p) for p in d["kfd_processes"]]
        drm={c["pci_chain"][0]["bdf"]:c for c in d["drm"] if c["pci_chain"]}
        gpus=[]
        for k in d["kfd"]:
            bdf=k["pci_chain"][0]["bdf"];card=drm[bdf];gpu_id=k["gpu_id"]
            positive=[{**p,"vram_bytes":p["vram_by_gpu_id"][gpu_id]} for p in owners if p["vram_by_gpu_id"].get(gpu_id,0)>0]
            gpus.append({"resource_id":node+"/gpu/"+bdf,"bdf":bdf,"logical_index_sysfs":int(k["properties"]["drm_render_minor"])-128,"hip_index_verified":False,"numa_node":int(k["pci_chain"][0]["numa_node"]),"kfd_gpu_id":gpu_id,"drm_card":card["card"],"render_minor":int(k["properties"]["drm_render_minor"]),"capacity_bytes":int(card["mem_info_vram_total"]),"sysfs_total_minus_used_bytes":card["total_minus_used_bytes"],"hip_free_bytes":None,"positive_vram_owners":positive,"headroom_over_2GiB_by_sysfs_only":card["total_minus_used_bytes"]>2*2**30,"functional_admission":False,"admission_gap":"HIP free and runtime initialization not verified; shared functional scope only", "performance_admission":False})
        mem={line.split(":",1)[0]:int(line.split()[1])*1024 for line in d["memory"].splitlines() if line.startswith(("MemTotal:","MemAvailable:"))}
        nodes[node]={"snapshot":str(path),"snapshot_sha256":sha(path),"hostname":d["hostname"],"boot_id":d["boot_id"],"started_utc":d["started_utc"],"ended_utc":d["ended_utc"],"cpu_count":d["cpu_count"],"memory":mem,"gpus":sorted(gpus,key=lambda g:g["logical_index_sysfs"]),"hca":d["hca"],"active_kfd_owners":owners,"zero_vram_owner_scope":"all zero VRAM owners remain active contexts, per-GPU execution scope unknown", "containers":[c for c in d["containers"] if c["name"].startswith("zjn")],"matching_vendor_images":[i for i in d["images"] if i["id"]=="sha256:ee3b9807d9e90cdc3e6ab9c1058f0b78dabcafa6a0fc7661d9c5af69ce1f9588"],"fluid_worker_pids":[p for p in d["process_summary"] if p.get("is_fluid_worker")],"errors":d["errors"]}
    pending=json.loads((args.input/"own_pending.json").read_text())
    cleanup=json.loads((args.input/"controller_cleanup.json").read_text()) if (args.input/"controller_cleanup.json").exists() else None
    nodes["node27"]={"status":"live_inventory_not_obtained","host_identity":"m03r2n07","pending_own_collector":{"pid":4037472,"start_time_ticks":"477235183","ppid":4037469,"pgid":4037469,"state":"D","wchan":pending["4037472"]["wchan"],"blocked_fd":"/sys/devices/virtual/kfd/kfd/proc/561325/vramtbl_45786","automatic_subprocess_timeout_sigkill_attempted":True,"exited_verified":False},"pending_own_controller":{"pid":4037469,"start_time_ticks":"477235176","pgid":4037469,"exited_verified":False},"additional_sampling_authorized":False}
    summary={"created_utc":datetime.now(timezone.utc).isoformat(),"status":"partial_28_30_complete_27_pending","nodes":nodes,"node29":{"accessed":False,"old_pending_unchanged":[{"container":"zjn-fluid-basic-1bce3897a2075b","pid":21883,"start_time_ticks":"475875814"},{"container":"zjn-fluid-basic-1bce3897a2075b","pid":21901,"start_time_ticks":"475875846"}]},"gpu_api_calls":0,"manual_signals":0,"automatic_timeout_kill_attempts":1,"runtime_ready":False,"performance_ready":False,"recommendations":{"node28":"Prefer own zjn-fluid-glm-full_distributed02 for metadata/function, protect its six workers and six keep-v02 workers; candidates GPUs2/3 or4/7 have sysfs headroom, HIP guard still required. No performance on shared host.","node30":"Create own isolated container from already-local exact vendor image; do not exec other-user containers. GPUs0/1 or4/7 are only functional candidates; external active vLLM contexts prevent empty/exclusive claim.","node27":"Stop all fresh runtime/sysfs owner-table attempts; preserve independent own pending and old protected services."}}
    if cleanup:
        summary["manual_signals"] = cleanup["signals_sent"]
        nodes["node27"]["pending_own_controller"]["exited_verified"] = cleanup["controller_absent_after"]
        nodes["node27"]["controller_cleanup"] = cleanup
    (args.out/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n")
    lines=["# 三节点资源调查：28/30完成，27只读采集待回收","","本次只读调查未调用HIP/SMI、未分配GPU、未访问node29、未改原XML/默认环境。node28/node30的SSH严格hostkey；known_hosts SHA保持。","","node27读取KFD vramtbl_45786时own collector进入D态；源脚本误纳入页表统计，已在后续源码排除，未重试。外层subprocess.run超时自动发出一次SIGKILL但未回收；这修正原未完成manifest中的signals=0，不能称完成或零signal。原始失败r01、r02和r03源码副本均保留。","","| 节点 | sysfs显存余量GiB | 已见owner | 容器/建议 |","|---|---|---|---|"]
    for node,d in nodes.items():
        if node=="node27":
            controller_status="controller4037469已absent" if cleanup and cleanup["controller_absent_after"] else "controller4037469未退出"
            lines.append(f"| node27 | 未取得新快照 | own采集PID4037472 D态 | 停止新尝试，{controller_status} |")
            continue
        free=", ".join(f"GPU{g['logical_index_sysfs']}={g['sysfs_total_minus_used_bytes']/2**30:.3f}" for g in d["gpus"])
        owner_text="外部SGLang在GPU0/1各52.431GiB；两组自有旧keeper在GPU2–7" if node=="node28" else "10个外部vLLM KFD owner当前vram_*为0；仍有活动上下文"
        container_text="自有full_distributed02与keep-v02均同vendor image/Python/DTK可用" if node=="node28" else "无自有container；准确vendor image已本地存在，可新隔离container"
        lines.append(f"| {node} | {free} | {owner_text} | {container_text} |")
    lines += ["","两机均8GPU/8NUMA/4HCA，4个HCA单端口报告400Gb/s且ACTIVE。ASPEED card0是显示卡，从GPU清单排除；GPU logical仅按KFD render minor登记，HIP索引未验。","","28与30均有image `sha256:ee3b9807d9e90cdc3e6ab9c1058f0b78dabcafa6a0fc7661d9c5af69ce1f9588`，tag为SGLang0.5.12/DTK2604。28两自有容器/usr/bin/python3为3.10.12、/opt/dtk与/dev/kfd可读写；这仅metadata，不认证库初始化。30只image元数据，没有进入其他用户容器。","","显存数字是sysfs total-used，不是HIP free，不构成最终2GiB准入或本任务预算。全部候选只支持下一步有界功能调查；共享进程/未知scope不允许性能就绪。旧14legacy仅本轮可见node28部分PID/容器取证，未RPC全目录验收；node29旧两pending与缺证保持。","","node27 pending独立记录于own_pending.json及own_pending_fd.json；不能依此对其他owner或旧keeper发送signal。" ]
    if cleanup:
        lines += ["","父授权后只向精确controller4037469/start477235176发一次SIGTERM，boot/UID/argv/PGID全匹配；controller已absent。未再次signal D child4037472，其start不变且D/PPID1，退出仍未证。总计一次自动timeout SIGKILL尝试与一次授权controller SIGTERM，不能称signals0。"]
    (args.out/"report.md").write_text("\n".join(lines)+"\n")
    (args.out/"manifest.json").write_text(json.dumps({"source_sha256":sha(Path(__file__)),"input_sha256":{p.name:sha(p) for p in args.input.iterdir() if p.is_file()},"files":{p.name:sha(p) for p in args.out.iterdir() if p.is_file()}},indent=2)+"\n")

if __name__=="__main__":
    main()
