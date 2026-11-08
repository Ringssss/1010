"""Record recovered node28/node30 init and copy evidence without altering failed runs."""
import hashlib
import json
from pathlib import Path


def durations(directory):
    events=[json.loads(line) for line in (directory/"child.events.jsonl").read_text().splitlines()]
    by_stage={event["stage"]:event for event in events}
    result={}
    for stage in ("torch_import","set_device"):
        first,last=by_stage.get(stage+"_enter"),by_stage.get(stage+"_exit")
        result[stage+"_seconds"]=(last["monotonic_ns"]-first["monotonic_ns"])/1e9 if first and last else None
    return result


def main():
    base=Path(__file__).resolve().parents[6]/"codex_coding/results/v0.1.23.3/task2/three_node_recovery"
    out=base/"diagnosis30_final_analysis_r01"
    out.mkdir(exist_ok=False)
    names=["probe_node30_init_r01","probe_node28_init_r01","probe_node28_ldpath_r01","probe_node28_hostipc_r01","probe_node28_sysnice_r01","probe_node28_observed_r01","probe_node28_diag60_r01","probe_node30_diag60_r01","probe_node28_copy60_r01","probe_node30_copy60_r01"]
    rows=[]
    for name in names:
        directory=base/name
        summary_path=directory/"summary.json"
        summary=json.loads(summary_path.read_text())
        child=summary.get("child_result") or {}
        registration=json.loads((directory/"registration.json").read_text())
        p=summary["process"]
        row={"run":name,"passed":summary["passed"],"deadline_seconds":p["hard_deadline_seconds"],
             "elapsed_including_cleanup_seconds":p["elapsed_ns"]/1e9,"timed_out":p["timed_out"],
             "last_stage":p.get("last_stage"),"signals":p["cleanup"]["signals"],"cleanup_passed":p["cleanup"]["passed"],
             "after_state":p["actual_after"]["state"],"source_sha256":registration["source_sha256"],
             "actual_bdf":child.get("actual_bdf"),"actual_hip_free":child.get("hip_free_bytes"),"payload_bytes":registration["payload_bytes"],
             "correctness":child.get("correctness"),"summary_path":str(summary_path),"summary_sha256":hashlib.sha256(summary_path.read_bytes()).hexdigest(),
             **durations(directory)}
        rows.append(row)
    success={row["run"]:row for row in rows if row["passed"]}
    assert all(name in success for name in names[-4:])
    for name in names[-2:]:
        assert success[name]["correctness"]["passed"] and success[name]["correctness"]["verified_bytes"]==1048576
    result={"runs":rows,"conclusion":"Both original private configurations complete init with a 60s bound and pass a full 1MiB round trip; 15s total can truncate a completing initialization.",
            "scope":"functional fresh init/copy only; shared load and no throughput result",
            "node30_storage_workaround":"source/results in original container's own private shm; root still had available0 in captured metadata",
            "original_source_preserved":"927f543e2af1fee3e10c540d90ef98fa76f49f1dd242426a99fb3a188a850b9b",
            "diagnostic_source":"d5331fa21f89df2428bce8faad3130bc41dbaa2a9b17b05fb0e5e042d217c90d",
            "mbind_error_scope":"Observed in owned child43 log; SYS_NICE test still15s failed and postcheck directory missing; no unique root-cause attribution",
            "node30_nccl_staged":"97a740672a2c1e526ecdc80ae4592c7438ff7edddc3c81a60ab120c28feaef11 + owned physical XML + deployment manifest, CPU only",
            "nccl_runtime_conclusion_here":None,"new_gpu_operations_by_this_summary":0,"new_remote_operations":0}
    (out/"summary.json").write_text(json.dumps(result,indent=2)+"\n")
    (out/"source.py").write_bytes(Path(__file__).read_bytes())
    a,b=success["probe_node28_diag60_r01"],success["probe_node30_diag60_r01"]
    report=f"""# node28/node30初始化阻断收敛与最小copy功能恢复\n\n原15秒失败记录全部保留。原private配置未增加LD或SYS_NICE，也未改IPC，在60秒诊断期限内两节点均完成新GPU初始化；两端随后各一次1MiB往返全SHA通过。这证明两配置具备可完成初始化的运行条件，原15秒总预算会截断此次可完成的工作；不把所有历史超时都永久归因于单一固定时长。当前是共享负载下的小功能证据，无性能/weights-ready样本。\n\nnode28总{a['elapsed_including_cleanup_seconds']:.9f}秒，Torch导入{a['torch_import_seconds']:.9f}秒、set_device {a['set_device_seconds']:.9f}秒，实际BDF{a['actual_bdf']}，HIP free {a['actual_hip_free']:,}B。node30总{b['elapsed_including_cleanup_seconds']:.9f}秒，Torch导入{b['torch_import_seconds']:.9f}秒、set_device {b['set_device_seconds']:.9f}秒，实际BDF{b['actual_bdf']}，HIP free {b['actual_hip_free']:,}B。两端自然退出、signals空、精确清理通过；copy实际全量1,048,576B SHA/长度通过，准确run原件及SHA见summary.json。父另聚合前后资产/owner/guard与独立C审，不用本摘要代替它们。\n\nnode30本轮guard先因/tmp ENOSPC阻断；准确CPU df/statvfs确认根盘与Dockerroot available0、inode有余。本任务没有删除、prune、停容器或改全局Docker配置。将同60秒copy源码d533按相同codex层级放入已有容器自有/dev/shm/fluidckpt_diag30_r01/project，ROOT与result自动指向shm，保留完整CID/StartedAt、privateIPC/原cap/LD/thread1；这是必要存储路径变动，不能和昨日15秒run称完全单变量。新shm source/结果及CPU namespace资产均有exact前后身份与SHA，未改原源927f或原厂XML。\n\nLD-only与hostIPC-only、SYS_NICE-only在15秒门槛下未恢复，故不能把它们的失败当整个配置永久不支持GPU。两份observer exact样本显示主线程CPU及RSS持续增长，促成一次较长时间边界诊断。owned child43库日志有mbind/seccomp自报告，但原cap后来能完成init，所以该错误不是目前已证明的必然阻断条件；SYS_NICE后hylog目录ENOENT，是否消失仍缺证。没有ptrace/privileged/unconfined扩权限。\n\n30NCCL准备只CPU写入runtime97a、自有gdr0物理XML与仅input_file重定位的deployment manifest，XML bytes/SHA不动；原probe927f、namespace0c307与frozen inventory8da563一并在shm同层保存。最新stage后shm余1,073,324,032B。NCCL实际验收由父/A/C对应新run确认，本报告不提前认运行成功或GDR/带宽。\n\n本agent全部实际远程操作为已授权CPU只读或本任务shm新文件stage；GPU均由父统一启动。新脚本/结果限diagnosis30范围，不触27/29，不全历史归档、不覆盖history-chat或旧失败。\n"""
    (out/"report.md").write_text(report)
    print(json.dumps({"out":str(out),"successful_init_and_copy_runs":len(success),"new_gpu_operations":0}))


if __name__=="__main__":main()
