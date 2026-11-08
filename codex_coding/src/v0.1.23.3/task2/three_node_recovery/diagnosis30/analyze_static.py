"""Summarize the single CPU snapshot and prepare one deferred environment hypothesis."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not args.out.name.startswith("diagnosis30_"):
        parser.error("require a new diagnosis30_* output directory")
    args.out.mkdir(parents=True, exist_ok=False)
    raw = args.input.read_bytes()
    data = json.loads(raw)
    env = data["container_env_static"]
    paths = {x["path"]: x for x in data["filesystem_paths"]}
    branch = env["branch_paths"]
    rocm_smi_branch = (not branch["/opt/hyhal/lib/cmake/rocm_smi"]["is_dir"]
                       or (branch["/opt/hyhal/lib/librocm_smi64.so"]["exists"]
                           and branch["/opt/hyhal/lib/librocm_smi64.so.1"]["exists"]))
    inherited = env["env_whitelist"]["LD_LIBRARY_PATH"]
    entries = inherited.split(":")
    insert_after = "/opt/dtk/opencl/lib"
    added = "/opt/dtk/.hyhal/rocm_smi/lib"
    assert data["identity_passed"] and rocm_smi_branch and added not in entries
    assert entries.count(insert_after) == 1
    entries.insert(entries.index(insert_after)+1, added)
    variant = ":".join(entries)
    size = json.loads(data["commands"]["container_size"]["stdout"])
    summary = {"input": str(args.input.resolve()), "input_sha256": hashlib.sha256(raw).hexdigest(),
               "sampled_utc": data["sampled_utc"], "identity_passed": data["identity_passed"],
               "root_available_bytes": paths["/"]["available_bytes"],
               "root_free_including_reserved_bytes": paths["/"]["free_bytes"],
               "root_available_inodes": paths["/"]["available_inodes"],
               "docker_root": data["commands"]["docker_root"]["stdout"].strip(),
               "container_size_logical": size,
               "alternate_disk": paths["/data/node30"],
               "alternate_parent": paths["/data/node30/shared"],
               "current_project_bind_on_root": True,
               "env_sh_sha256": env["env_sh"]["sha256"],
               "env_sh_sourced": False, "static_rocm_smi_branch_predicted_true": rocm_smi_branch,
               "deferred_single_variable": {"key": "LD_LIBRARY_PATH", "inherited": inherited, "variant": variant,
                                             "added_directory": added, "placement": "after /opt/dtk/opencl/lib; other entries and order preserved",
                                             "runtime_causality_established": False},
               "gpu_operations": 0, "remote_file_writes": 0,
               "remote_signals": sum(len(c.get("signals", [])) for c in data["commands"].values()),
               "gpu_schedule": "paused because root-backed bootstrap/outputs cannot be written by current host user"}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False)+"\n")
    (args.out / "source.py").write_bytes(Path(__file__).read_bytes())
    project = "/home/zhujianian/zhujianian/fluidckpt_zjn"
    gate = project + "/codex_coding/src/v0.1.23.3/task2/three_node_recovery/probe/init_gate.py"
    identity = data["container_identity"]
    proposed = []
    for name, ld in (("probe_node30_control_r02", None), ("probe_node30_ld_rocm_r01", variant)):
        argv = ["docker", "exec", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "OMP_NUM_THREADS=1", "-e", "MKL_NUM_THREADS=1"]
        if ld is not None:
            argv += ["-e", "LD_LIBRARY_PATH="+ld]
        argv += [identity["id"], "python3", gate, "--node", "node30", "--container-id", identity["id"],
                 "--container-started-at", identity["started"], "--host-boot-id", data["boot_id"],
                 "--expected-container-hostname", data["hostname"], "--gpu", "0", "--bdf", "0000:09:00.0",
                 "--visibility", "selected", "--stage", "init", "--out",
                 project+"/codex_coding/results/v0.1.23.3/task2/three_node_recovery/"+name]
        proposed.append({"run_name": name, "argv_in_host": argv, "shell_in_host": shlex.join(argv), "executed": False})
    (args.out / "deferred_commands.json").write_text(json.dumps({"authorization": "parent selects and runs after writeability and fresh protection guards; no execution by this script", "preconditions": ["root-backed result/stage writeability restored, or reviewed output relocation preserving identity and watchdog", "new exact host/container and source SHA guard", "same-day original selected visibility init with OMP/MKL=1", "no unresolved own process cleanup before variant", "before/after original XML and visible GPU-owner protection"], "commands": proposed}, indent=2)+"\n")
    report = f"""# node30初始化调查：当前写入阻断与静态环境差异\n\n{data['sampled_utc']}一次stdin只读采样，同host boot、完整容器ID/StartedAt/image/task label及原PID namespace连续。根盘、/tmp与DockerRootDir=/var/lib/docker的available均0B，root保留块在内的free为{paths['/']['free_bytes']:,}B，仍有{paths['/']['available_inodes']:,}个inode。父本轮guard此前已实际mktemp ENOSPC；当前停止GPU，不以os.access可写代替实际有可分配空间。\n\n自有isolated容器SizeRw={size['size_rw']:,}B（约{size['size_rw']/2**20:.2f}MiB），SizeRootFs={size['size_root_fs']:,}B。它们是逻辑大小，不含宿主日志/bindmount，也不是物理块归属；本次没有扫描其他服务或归因满盘来源。另一本地/data/node30可用{paths['/data/node30']['available_bytes']:,}B（约{paths['/data/node30']['available_bytes']/2**40:.3f}TiB）；shared父目录os.access W=true、zhujianian子目录不存在，未做mkdir/写入验证。当前容器没有该盘挂载，项目bind仍指向根盘/tmp，不能只改宿主bootstrap到另一盘就声称容器结果可写。\n\n完整/opt/dtk/env.sh SHA={env['env_sh']['sha256']}与旧metadata一致，未source。脚本rocm_smi条件为“不存在cmake目录，或两so同时存在”；本次cmake目录和两so均存在，所以静态条件预测true。HYHAL fallback与hy-smi fallback静态条件false。source预计在现有LD_LIBRARY_PATH中增加/opt/dtk/.hyhal/rocm_smi/lib，同时也改PATH、DTKROOT、AMDGPU_TARGETS、MIOPEN_FIND_MODE和编译路径；静态预测不是已执行环境delta。\n\n存储条件恢复后先跑同日原配置control，不能把隔一天的成功直接归因变体。保持同容器/GPU0/BDF/selected visibility、总15sdeadline、core0、零payload、OMP_NUM_THREADS=1/MKL_NUM_THREADS=1。现run_gate默认host_gate只传PYTHONDONTWRITEBYTECODE，而原r01 native命令还显式两线程变量；对照须保留线程env，以免加入另一变化。\n\n如control仍停set_device，再只改一个LD_LIBRARY_PATH变量：保留原entry顺序，在/opt/dtk/opencl/lib后插入/opt/dtk/.hyhal/rocm_smi/lib；不source全脚本、不改IPC/cap/memory/seccomp。预期：若这一库搜索差异充分解释失败，variant应在相同15s内出现set_device_exit、BDF0000:09:00.0和实际HIPfree、通过init并exact回收；若仍同阶段超时，该增量不足以恢复，不继续copy/XML。若control先成功，本轮仅证明原配置当前可用，无需启动变体。control失败+variant成功仍需必要一次原配置回退确认以区分时间/驱动状态变化，且每配置通常一次、必要control第二次。\n\n保留旧失败、冻结probe/source和生产XML；所有GPU仍由父独立准入/执行。deferred_commands.json为未执行argv，不能直接用当前根盘状态运行。collector的本地自然退出/精确timeout回收/缺路径显式记录CPU3项通过；远端6项CPU命令全部自然退出、signal0，无device open/library load/Torch/SMI，无远端文件写入。未知：原15s超时唯一原因、历史hylog open errno、根盘满归属、容器ioctl权限、另一盘实际写入及未来GPU运行。\n"""
    (args.out / "report.md").write_text(report)
    print(json.dumps({"out": str(args.out), "root_available_bytes": summary["root_available_bytes"], "rocm_smi_branch_predicted_true": rocm_smi_branch, "gpu_operations": 0}))


if __name__ == "__main__":
    main()
