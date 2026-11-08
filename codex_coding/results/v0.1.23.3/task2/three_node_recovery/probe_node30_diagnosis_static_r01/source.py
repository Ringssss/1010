"""CPU-only comparison of failed node30 fresh init and existing container evidence."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
BASE = ROOT / "codex_coding/results/v0.1.23.3/task2/three_node_recovery"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    out = BASE / "probe_node30_diagnosis_static_r01"
    out.mkdir(exist_ok=False)
    paths = {"setup": BASE / "inventory_setup_node30_r01/stdout.json", "inventory": BASE / "inventory_r03/node30.stdout.json",
             "events": BASE / "probe_node30_init_r01/child.events.jsonl", "process": BASE / "probe_node30_init_r01/child.process.json",
             "log": BASE / "probe_node30_init_r01/child.log",
             "historical_launcher": ROOT / "codex_coding/src/task2/cluster/run_system.py",
             "historical_container": ROOT / "codex_coding/results/v0.1.23.3/task2/topology_alignment/provenance_r01/node27/container.json"}
    setup = json.loads(paths["setup"].read_text())
    inv = json.loads(paths["inventory"].read_text())
    process = json.loads(paths["process"].read_text())
    events = [json.loads(line) for line in paths["events"].read_text().splitlines()]
    enter = next(r["monotonic_ns"] for r in events if r["stage"] == "torch_import_enter")
    exit = next(r["monotonic_ns"] for r in events if r["stage"] == "torch_import_exit")
    current = setup["container"]
    same_image = [{"name": c["name"], "id": c["id"], "image_id": c["image_id"], "started": c.get("started"),
                   "host_config": c.get("host_config"), "env_whitelist": c.get("env_whitelist"), "mounts": c.get("mounts")}
                  for c in inv["containers"] if c.get("image_id") == current["image_id"]]
    result = {"node": "node30", "torch_import_seconds": (exit-enter)/1e9, "failure_boundary": process["last_stage"],
              "hard_deadline_seconds": process["hard_deadline_seconds"], "process_elapsed_seconds": process["elapsed_ns"]/1e9,
              "cleanup_passed": process["cleanup"]["passed"], "signals": process["cleanup"]["signals"],
              "payload_bytes": 0, "actual_bdf_observed": False, "actual_hip_free_observed": False,
              "warning": paths["log"].read_text().strip(), "warning_is_unique_cause": False,
              "fresh_container": current, "same_image_existing_containers": same_image,
              "confirmed_config_differences": ["private IPC vs historical successful isolated host IPC",
                 "IPC_LOCK only vs historical isolated SYS_NICE", "8GiB memory/pids512/shm1GiB vs historical launcher unspecified memory/pids",
                 "direct python image environment vs historical source /opt/dtk/env.sh", "narrow project bind vs historical home/model binds"],
              "present_required_config": ["kfd", "mkfd", "dri", "infiniband", "hyhal read-only", "memlock unlimited", "core0", "host network"],
              "unresolved": ["effective fresh environment versus sourced DTK environment", "actual UID/GID/device DAC/capabilities/seccomp",
                             "log directory absence versus permissions", "runtime library overlay/version vs existing mapped process libraries",
                             "IPC/capability/resource-limit effect; no controlled runtime comparison", "independent node30 runtime/driver condition"],
              "source_refs": [{"role": key, "path": str(path), "sha256": sha(path)} for key, path in paths.items()],
              "gpu_operations_by_analysis": 0, "copy_nccl_stopped": True}
    (out / "diagnosis.json").write_text(json.dumps(result, indent=2) + "\n")
    (out / "source.py").write_bytes(Path(__file__).read_bytes())
    (out / "diagnosis.md").write_text("""# node30 fresh 初始化静态诊断

Torch 2.10.0/HIP 6.3.26113 导入约2.096秒完成，随后停在 `set_device_enter`；15秒外部deadline后TERM，own PID13/start477410700/PGID13退出与组回收通过。没有实际BDF、HIPfree、payload或communicator，不把本次失败叫XML错误或硬件永久故障。copy/NCCL停止。

fresh实际已提供kfd/mkfd/dri/infiniband、hyhal只读、hostnetwork、无限memlock、core0，不能称简单漏掉这些设备/mount。历史成功isolated launcher同样非privileged、hyhal只读，并用hostIPC+SYS_NICE；fresh是privateIPC+IPC_LOCK、shm1GiB、memory8GiB、pids512。历史launch还先source /opt/dtk/env.sh，而fresh native Python继承image ENV。这些是待解释配置差异，当前没有单变量runtime证据排序其因果。旧vendor容器常privileged不能反推fresh必须privileged；不改共享权限/driver。

`Could not open /var/log/hylog/.`只直接证明一次日志路径open失败，当前未取得其errno/目录存在性/UID权限/实际调用点；不认唯一根因。fresh仅bind项目和hyhal，旧容器不同home/model挂载与此单卡零payload没有已证明关系。same-image既有容器列表只认证镜像/配置，已有长寿命GPU进程不等于同配置fresh初始化已通过。

下一步仅一次CPU-only只读取证：在同fullID/StartedAt/boot容器中运行runtime_metadata.py，8秒内部alarm/外部10秒；读effective whitelist ENV、静态env.sh相关赋值（不source）、UID/groups、CapEff/CapBnd/Seccomp/NoNewPrivs、limits、设备stat major/minor/mode/os.access、hylog目录存在性与mountinfo。host补inspect User/GroupAdd/SecurityOpt/CapAdd/ReadonlyRootfs、cgroup memory.events/pids.current。不导入Torch、不loadHIP、不openGPU设备、不SMI，不建新容器/改权限/加cap或延长GPUdeadline。父统一stage源码SHA、执行与collect，分析agent没有发起远端操作。

原始事实、同镜像配置及source SHA见diagnosis.json。当前只做原因范围收窄，不给新的GPU实验或容器重建建议。
""")
    print(json.dumps({"out": str(out), "gpu_operations": 0, "copy_nccl_stopped": True}))


if __name__ == "__main__":
    main()
