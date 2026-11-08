"""Record the executed LD-only failure as a new delta without changing its plan."""
import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[6]
    base = root / "codex_coding/results/v0.1.23.3/task2/three_node_recovery"
    out = base / "diagnosis30_node28_lddelta_r01"
    out.mkdir(exist_ok=False)
    refs = [base / "diagnosis30_node28_ldplan_r01/launch.json", base / "probe_node28_ldpath_r01/summary.json",
            base / "probe_node28_ldpath_r01/child.events.jsonl", base / "probe_node28_ldpath_r01/source.py",
            base / "admission28_guard_after_ldpath_r01/result.json", base / "review_runtime_node28_ldpath_r01/summary.json"]
    summary = json.loads(refs[1].read_text())
    process = summary["process"]
    events = [json.loads(x) for x in refs[2].read_text().splitlines()]
    index = {x["stage"]: x for x in events}
    assert process["timed_out"] and process["last_stage"] == "set_device_enter" and "set_device_exit" not in index
    assert process["cleanup"]["passed"] and process["cleanup"]["signals"] == ["TERM"]
    assert process["actual_after"]["state"] == "absent" and process["cleanup"]["pending_group_members"] == []
    guard = json.loads(refs[4].read_text())
    assert guard["identity_checks_passed"]
    seconds = (index["torch_import_exit"]["monotonic_ns"]-index["torch_import_enter"]["monotonic_ns"])/1e9
    # Logs were collected by the parent into launch result directories.
    logs = {}
    for name in ("admission28_launch_init_r01", "diagnosis30_node28_ldplan_r01"):
        candidates = [base/name/"stderr.txt", base/name/"stdout.txt"]
        logs[name] = [{"path": str(p), "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in candidates if p.exists()]
    result = {"applies_to_node": "node28", "planned_then_executed_by_parent": True,
              "plan_preserved": str(refs[0].parent), "run_name": "probe_node28_ldpath_r01",
              "runtime_passed": summary["passed"], "torch_import_seconds": seconds,
              "set_device_exit_observed": False, "hard_deadline_seconds": 15,
              "elapsed_including_cleanup_seconds": process["elapsed_ns"]/1e9,
              "exact_owner": {k: process["owner"][k] for k in ("pid", "start_time", "pgid", "sid", "boot_id", "pid_namespace")},
              "cleanup_passed": True, "signals": ["TERM"], "pending_group_members": [],
              "child_result_present": summary["child_result"] is not None, "hip_bdf": None, "hip_free": None,
              "after_guard_identity_passed": guard["identity_checks_passed"],
              "conclusion": "Adding only /opt/dtk/.hyhal/rocm_smi/lib is insufficient to complete this initialization within the unchanged total deadline.",
              "not_established": ["all environment causes excluded", "actual loaded rocm_smi library changed", "unique hardware/driver root cause", "permanent GPU failure"],
              "sources": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in refs],
              "offline_only": True, "new_remote_operations": 0, "new_gpu_operations": 0}
    (out/"summary.json").write_text(json.dumps(result, indent=2)+"\n")
    (out/"source.py").write_bytes(Path(__file__).read_bytes())
    (out/"report.md").write_text(f"""# node28 LD单变量实测增量\n\n父已执行原计划probe_node28_ldpath_r01；原diagnosis30_node28_ldplan_r01保持当时未执行的计划快照，本目录单独记录执行结果。新增目录/opt/dtk/.hyhal/rocm_smi/lib不够让本配置在原总15秒期限内通过初始化。未排除其他ENV原因，不认为永久坏卡或已获唯一根因。\n\nTorch导入{seconds:.9f}秒；只有set_device_enter，无exit/BDF/HIPfree。supervisor含回收总{process['elapsed_ns']/1e9:.9f}秒，timed_out=true、rc=-15，child.result未生成。child28/start485301163/PGID28/SID28由精确一次TERM回收，absent、组空；同boot/fullID/StartedAt后置guard在06:25:54UTC通过。源码927f543e...、OMP/MKL1、GPU2/BDF55、selected和总deadline维持原配置，准确argv见原plan launch.json。实际载入的rocm_smi库路径没有采样，不能把库搜索目录设置当成库已换版本的证明。\n\n独立C审查另记录baseline child.log有32B hylog警告，本LD组child.log为0B。可确认本次警告没有复现，而set_device阻断继续；日志变化不说明那条警告是超时的必要/充分原因，open errno与调用位置仍未知。\n\n父下一步按独立新容器hostIPC-only变体推进，恢复原继承LD；这次LD设置仅docker exec进程级，没有改全局环境。新容器必须保持其他限制/挂载/设备/cap以及同probe，并明确容器身份变化是IPC对照所必需的新身份。若也失败，再优先从新own child取得直接线程/系统调用等待证据，不继续无证据叠加cap。\n\n建议的下一项只读观察设计（未实现、未执行）：由新GPU run的独立CPU观察者，在owner注册和set_device_enter均已出现后，对这个run确切PID/start/PGID/SID/argv、boot和PID namespace前后验证，再限量采/proc/PID/task/TID的stat/status/wchan/syscall及本人FD symlink，最多32线程/16FD，两次小快照且总run15秒不延长。不打开FD目标，不读KFD/sysfs vramtbl，不ptrace attach/加cap；permission denied显式记录为空缺。运行前设计好观察者自身deadline与精确CPU回收；原GPU supervisor仍唯一负责child信号。\n\n观测预期：长期R且utime快速累积、syscall为running支持用户态旋转；S且futex/wchan等待支持线程同步等待；ioctl及目标fd或D+wchan支持内核/驱动调用等待。这些只定位等待层，不能据一个symbol认唯一根因。Python堆栈如只停在torch C入口仍不足以区分上述三类。观察者若权限拒绝，不因此直接升级SYS_PTRACE/privileged；父据证据另决定。\n\n本增量仅本地离线读取原件，没有新远程或GPU操作。完整原件SHA、准确owner与失败边界见summary.json；独立运行审查见review_runtime_node28_ldpath_r01。\n""")
    print(json.dumps({"out": str(out), "runtime_passed": False, "torch_import_seconds": seconds, "new_remote_operations": 0}))


if __name__ == "__main__":
    main()
