"""Freeze node qualification from saved evidence; never call remote hosts or GPUs."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[6]
BASE=ROOT/"codex_coding/results/v0.1.23.3/task2/three_node_recovery"

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--node30-runtime-status",choices=("pending_parent","passed","failed"),default="pending_parent")
    p.add_argument("--node30-runtime-evidence",type=Path,action="append",default=[])
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False)
    if a.node30_runtime_status!="pending_parent" and not a.node30_runtime_evidence:p.error("parent runtime result requires saved evidence")
    summary_path=BASE/"inventory_summary_r03/summary.json";summary=json.loads(summary_path.read_text())
    storage_path=BASE/"inventory_dockerroot_node28_r01/stdout.json";storage=json.loads(storage_path.read_text())
    disk_path=BASE/"inventory_disk_node28_r01/stdout.json"
    setup_path=BASE/"inventory_setup_node30_r01/stdout.json";setup=json.loads(setup_path.read_text())
    guard_path=BASE/"inventory_guard_node30_before_r01/stdout.json";guard=json.loads(guard_path.read_text())
    assert storage["docker_root_dir"]=="/var/lib/docker"
    lines=storage["actual_root_df_bytes"]["stdout"].splitlines()
    available=int(lines[-1].split()[3]);assert available==0
    assert all(guard["container_identity_checks"].values())
    ci=setup["container"];assert ci["id"]==guard["container"]["id"]
    sources=[summary_path,storage_path,disk_path,setup_path,guard_path,BASE/"inventory_setup_node28_r02/command.json",BASE/"inventory_r03/partial_completion.json",*a.node30_runtime_evidence]
    manifest={"schema":"fluidckpt.three_node_qualification.v1","development":"v0.1.23.3/task2","created_utc":datetime.now(timezone.utc).isoformat(),"built_offline":True,"evidence":[{"path":str(f.resolve()),"sha256":sha(f)} for f in sources],"nodes":{},"node29":{"accessed_by_this_task":False,"old_pending_unchanged":summary["node29"]["old_pending_unchanged"]},"performance_ready":False,"measurement_samples_added":0}
    manifest["nodes"]["node27"]={"status":"runtime_and_collector_blocked","fresh_inventory_obtained":False,"physical_map_evidence":"historical resume_map_r02 only, not current availability","runtime_accepted":False,"selected_gpu_qualification":False,"pending_own_collector":summary["nodes"]["node27"]["pending_own_collector"],"controller_exited_verified":True,"further_runtime_or_vramtable_attempts":False,"performance_ready":False}
    manifest["nodes"]["node28"]={"status":"storage_blocked","hostname":storage["hostname"],"boot_id":storage["boot_id"],"resource_snapshot":summary["nodes"]["node28"]["snapshot"],"resource_snapshot_current_live_claim":False,"gpu_count":8,"hca_count":4,"docker_root_dir":storage["docker_root_dir"],"docker_root_available_bytes":available,"new_isolated_container_created":False,"new_container_attempts_stopped":True,"alternative_scratch_plan":"inventory_setup_plan_r07, offline only; not authorized for execution after full Docker root confirmed","runtime_accepted":False,"performance_ready":False,"actions_not_taken":["delete user data","move Docker global root","reuse keeper container for new probes","GPU startup"]}
    manifest["nodes"]["node30"]={"status":"isolated_container_ready_runtime_pending" if a.node30_runtime_status=="pending_parent" else "runtime_functional_scope_passed" if a.node30_runtime_status=="passed" else "runtime_probe_failed","hostname":guard["hostname"],"boot_id":guard["boot_id"],"resource_snapshot":summary["nodes"]["node30"]["snapshot"],"gpu_count":8,"hca_count":4,"selected_gpu":{"logical_sysfs":0,"bdf":"0000:09:00.0","hip_mapping_verified":False},"isolated_container":{"id":ci["id"],"started_at":ci["State"]["StartedAt"],"image_id":ci["image_id"],"stage":setup["stage"]},"before_container_identity_checks":guard["container_identity_checks"],"source_asset_sha_checks":{k:v["sha256"]==v["expected_sha256"] for k,v in guard["source_assets"].items()},"node30_runtime_status":a.node30_runtime_status,"runtime_accepted":a.node30_runtime_status=="passed","runtime_evidence_paths":[str(f.resolve()) for f in a.node30_runtime_evidence],"hip_free_guard_verified":False,"copy_validated":False,"owned_xml_runtime_accepted":False,"cross_node_communication_validated":False,"external_kfd_contexts_present":True,"performance_ready":False,"runtime_scope":"status is parent-reported init only; copy/XML/network require their own saved result"}
    manifest["signals"]={"node27_automatic_timeout_sigkill_attempts":1,"node27_authorized_exact_controller_sigterm":1,"node30_exact_probe_sigterm":0,"total_attempts":2,"node29_signals":0}
    if a.node30_runtime_status=="failed":
        init_path=BASE/"probe_node30_init_r01/summary.json";init=json.loads(init_path.read_text())
        process=init["process"]
        assert not init["passed"] and process["timed_out"] and process["last_stage"]=="set_device_enter"
        assert process["cleanup"]["signals"]==["TERM"] and process["cleanup"]["passed"] and process["actual_after"]["state"]=="absent"
        assert process["cleanup"]["pending_group_members"]==[]
        post_path=BASE/"inventory_guard_node30_after_init_r01/stdout.json";post=json.loads(post_path.read_text())
        assert all(post["container_identity_checks"].values())
        events=[json.loads(line) for line in (BASE/"probe_node30_init_r01/child.events.jsonl").read_text().splitlines()]
        stages={e["stage"]:e for e in events}
        manifest["nodes"]["node30"].update(status="initialization_timeout",last_stage="set_device_enter",hard_deadline_seconds=process["hard_deadline_seconds"],torch_import_seconds=(stages["torch_import_exit"]["monotonic_ns"]-stages["torch_import_enter"]["monotonic_ns"])/1e9,new_payload_bytes=0,new_communicators=0,exact_probe_cleanup={"pid":process["pid"],"start_time_ticks":process["owner"]["start_time"],"signals":process["cleanup"]["signals"],"exited_verified":True,"pending_group_members":[]},post_guard_container_identity_checks=post["container_identity_checks"],full_post_guard_independent_review="pending_at_manifest_creation",further_copy_xml_or_performance_attempts=False)
        manifest["signals"].update(node30_exact_probe_sigterm=1,total_attempts=3)
    (a.out/"qualification.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
    status30=manifest["nodes"]["node30"]["status"]
    lines=["# 三节点资格清单","","| 节点 | 当前资格 | 缺口 |","|---|---|---|","| node27 | 新资源采集未完成；runtime受阻 | own collector4037472/start477235183退出未证；停止新尝试 |","| node28 | storage_blocked | 实际Dockerroot=/var/lib/docker，同根盘available0B；停止新容器方案 |",f"| node30 | {status30} | copy、自有XML与跨节点功能均未认证 |","","node30容器fullID与StartedAt绑定原setup及guard；存在外部KFD上下文，performance_ready=false。node28的/dev/shm备用方案仅离线准备，不能据此绕过Dockerroot满。三节点状态分开，不把30单节点资格写成三节点通信或性能就绪。node29从未访问，旧两个exact pending保持。","","此清单从冻结文件离线生成，不执行GPU、SSH、Docker或模型读取。runtime状态待父的真实结果时保持pending，不把构建CPU检查当运行通过。"]
    if a.node30_runtime_status=="failed":
        lines += ["","node30 Torch导入2.096s完成，停在set_device_enter，15s hard deadline后仅对本次PID13/start477410700精确TERM；absent且group无残留。新增payload0/communicator0，未进入copy或XML。after guard container身份全true，但完整owner/内存保护独立审查尚在进行。","","本轮signal记录：node27 timeout自动KILL尝试1次、授权精确controllerTERM1次；node30 exact probeTERM1次，共3次。node27新D pending与node29旧21883/start475875814、21901/start475875846三项账本均保留；不是signals0或全部退出。"]
    (a.out/"report.md").write_text("\n".join(lines)+"\n")
    (a.out/"source.py").write_bytes(Path(__file__).read_bytes())
    print(json.dumps({"out":str(a.out),"node28_status":"storage_blocked","node30_runtime_status":a.node30_runtime_status,"remote_calls":0}))

if __name__=="__main__":main()
