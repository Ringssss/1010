"""Summarize exact observer evidence and the new owned mbind log without rerunning."""
import hashlib
import json
from pathlib import Path


def stat_fields(raw):
    fields = raw.rsplit(") ", 1)[1].split()
    return {"state": fields[0], "minflt": int(fields[7]), "utime_ticks": int(fields[11]),
            "stime_ticks": int(fields[12]), "start_time": fields[19], "vsize_bytes": int(fields[20]),
            "rss_pages": int(fields[21])}


def main():
    root = Path(__file__).resolve().parents[6]
    base = root/"codex_coding/results/v0.1.23.3/task2/three_node_recovery"
    out = base/"diagnosis30_observed_analysis_r01"
    out.mkdir(exist_ok=False)
    observer_path = base/"diagnosis30_node28_observer_r01/summary.json"
    probe_path = base/"probe_node28_observed_r01/summary.json"
    hylog_path = base/"diagnosis30_observed_postcheck_r01/hylog.txt"
    pid_path = base/"diagnosis30_observed_postcheck_r01/pid.stdout.txt"
    observed = json.loads(observer_path.read_text())
    probe = json.loads(probe_path.read_text())
    assert observed["exact_samples"] == 2 and probe["process"]["timed_out"]
    rows = []
    for snap in observed["snapshots"]:
        assert snap["exact_before"] and snap["exact_after"]
        leader = next(t for t in snap["threads"] if t["tid"] == observed["target_owner"]["pid"])
        fields = stat_fields(leader["stat"]["value"])
        fields.update(monotonic_ns=snap["monotonic_ns"], seconds_after_set_device=snap["actual_seconds_after_set_device_enter"],
                      wchan=leader["wchan"], syscall=leader["syscall"],
                      other_threads=[{"tid": t["tid"], "state": stat_fields(t["stat"]["value"])["state"], "wchan": t["wchan"], "syscall": t["syscall"]} for t in snap["threads"] if t is not leader],
                      fds=snap["fds"])
        rows.append(fields)
    delta = {key: rows[1][key]-rows[0][key] for key in ("utime_ticks", "stime_ticks", "rss_pages", "vsize_bytes", "minflt")}
    delta["cpu_total_ticks"] = delta["utime_ticks"]+delta["stime_ticks"]
    delta["wall_seconds"] = (rows[1]["monotonic_ns"]-rows[0]["monotonic_ns"])/1e9
    refs = [observer_path, probe_path, hylog_path, pid_path]
    sources = [{"url": "https://docs.docker.com/engine/security/seccomp/", "retrieved_date": "2026-10-09", "scope": "official default seccomp and CAP_SYS_NICE context"},
               {"url": "https://github.com/moby/profiles/blob/main/seccomp/default.json", "retrieved_date": "2026-10-09", "scope": "upstream current main; NUMA syscall allow group includes CAP_SYS_NICE; not node28 deployed profile proof"},
               {"url": "https://github.com/moby/profiles/blob/main/seccomp/seccomp_linux.go", "retrieved_date": "2026-10-09", "scope": "upstream current main; includes.Caps resolved from OCI bounding capabilities; not node28 runtime proof"}]
    summary = {"node": "node28", "exact_samples": 2, "leader": rows, "delta": delta,
               "clock_ticks_per_second_measured": None, "page_size_measured_on_node28": None,
               "owned_hylog": hylog_path.read_text(), "owned_process_exit_postcheck": pid_path.read_text(),
               "initialization_passed": False, "hip_free": None, "actual_bdf": None,
               "conclusion": "Main thread remains runnable and CPU counters plus resident pages increase; the owned thunk log reports mbind blocked by seccomp. Test only SYS_NICE before a longer deadline.",
               "not_proven": ["useful initialization progress", "absence of retry/spin", "actual mbind errno/filter match", "all seccomp policy rules on node28", "permanent GPU failure"],
               "diagnostic60_prepared": False, "diagnostic60_executed": False,
               "next_parent_test": "SYS_NICE only, original private IPC/LD/thread/GPU/probe/deadline kept; child A owns new container implementation",
               "raw_refs": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in refs],
               "official_sources": sources, "new_remote_operations": 0, "new_gpu_operations": 0}
    (out/"summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False)+"\n")
    (out/"source.py").write_bytes(Path(__file__).read_bytes())
    report = f"""# node28两份线程快照与mbind直接日志增量\n\n主线程43/start485348862在set_device后约2.000/6.000秒的两份exact样本均为R/wchan0；utime {rows[0]['utime_ticks']}→{rows[1]['utime_ticks']}、stime {rows[0]['stime_ticks']}→{rows[1]['stime_ticks']}，约{delta['wall_seconds']:.6f}秒间隔增加{delta['cpu_total_ticks']} ticks。RSS {rows[0]['rss_pages']}→{rows[1]['rss_pages']}页，增加{delta['rss_pages']:,}页；vsize增{delta['vsize_bytes']:,}B。节点CLK_TCK和PAGESIZE本次没有采，因此保留原单位；若分别为100Hz/4KiB，约3.99 CPU秒及468.46MiB驻留内存增量。RSS是进程DRAM页观察，不是HBM free、context成功或有用工作进度。证据支持持续消耗CPU/内存，也允许初始化工作、重试或旋转；尚不能判无进展死锁。\n\n辅助线程44为S、wchan=kfd_wait_on_events，这不是主线程同样卡在KFD的证明。两份所有syscall读取均EPERM，所以没有系统调用号/参数/errno。FD样本已见kfd、hsakmt_shared_mem、8个render设备和FD6自有hylog；说明该child已实际打开这些对象，不能从open成功推导全部ioctl或运行成功。两个sample前后owner完整一致；supervisor15秒仍失败，但TERM exact回收、组空、afterguard通过。父后置单独核observer31与child43 /procabsent，原件见postcheck。\n\n父从FD6明确路径`/var/log/hylog/root_python3.10_20261009_143318.930369_43.log`取到本child的库日志：`mbind is blocked by seccomp`，位置为vendor ROCT-Thunk-Interface_6.3_docker/src/fmm.c:1831。它比无所属的目录open警告更有针对性，是本child初始化路径上的库错误自报告；实际mbind syscallerrno与有效seccomp规则尚未直接抓到。\n\n[Docker官方文档](https://docs.docker.com/engine/security/seccomp/)说明默认filter按allowlist处理，并列mbind与CAP_SYS_NICE相关。[Moby当前上游profile](https://github.com/moby/profiles/blob/main/seccomp/default.json)对mbind及相关NUMA策略调用的ALLOW组设置CAP_SYS_NICE条件；[profile构建实现](https://github.com/moby/profiles/blob/main/seccomp/seccomp_linux.go)从OCI bounding capabilities筛选该条件。这是官方机制证据，当前main不等于node28安装的准确Docker版本/profile。增加SYS_NICE在常见该实现下既改变cap，也会改变默认生成filter中相应允许项；单个cap配置对照成功可证明该配置恢复运行，仍不能独自分离内核cap权限与filter规则的贡献。\n\n父已暂停60秒源码/计划，我尚未生成该copy或命令。下一项为A实现SYS_NICE-only自有新容器：原IPC_LOCK保留、只新增SYS_NICE，privateIPC/原LD/thread1/GPU2/probe927f/15秒和其他限制保持；不unconfined/privileged。父/C核新身份、CapEff/CapBnd和Seccomp保持启用、self-owned日志及init结果。若错误不再出现且15秒init通过，支持该配置是可用修复；若错误不再出现但仍超时，只说明报错路径改变且不足恢复，再结合当前CPU/内存证据决定一次60秒初始化诊断。若同错误仍出现，先核cap与有效filter事实，不继续猜其他cap。\n\n没有新远程或GPU操作，旧计划/源码/失败原件不改；本目录为独立离线解读与下一项实验的判别预期，原件SHA和官方链接见summary.json。\n"""
    (out/"report.md").write_text(report)
    print(json.dumps({"out": str(out), "delta": delta, "diagnostic60_prepared": False, "new_remote_operations": 0}))


if __name__ == "__main__":
    main()
