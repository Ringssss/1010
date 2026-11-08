"""Read node28 static environment once and prepare, without running, an LD-only init."""
import argparse
import hashlib
import inspect
import json
from pathlib import Path
import shlex

import collect_storage_env as cpu


REMOTE_MAIN = '''
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
row = {"hostname": os.uname().nodename, "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(), "remote_file_writes": 0, "gpu_operations": 0}
assert row["hostname"] == SPEC["hostname"] and row["boot_id"] == SPEC["boot_id"]
fmt = '{"id":{{json .Id}},"started":{{json .State.StartedAt}},"running":{{json .State.Running}},"image":{{json .Image}},"task_label":{{json (index .Config.Labels "fluidckpt.task")}}}'
def ident():
    command = bounded(["docker", "inspect", "--format", fmt, SPEC["container_id"]], 3)
    assert command["returncode"] == 0 and not command["timed_out"]
    ci = json.loads(command["stdout"])
    assert ci["id"] == SPEC["container_id"] and ci["started"] == SPEC["container_started"] and ci["running"] is True and ci["image"] == SPEC["image_id"] and ci["task_label"] == "v0.1.23.3/task2/three_node_recovery"
    return command
row["identity_before"] = ident()
row["static_command"] = bounded(["docker", "exec", "-i", SPEC["container_id"], "python3", "/dev/stdin"], 5, CONTAINER_SOURCE.encode())
assert row["static_command"]["returncode"] == 0 and not row["static_command"]["timed_out"]
row["static_env"] = json.loads(row["static_command"]["stdout"])
row["identity_after"] = ident()
row["identity_passed"] = True
print(json.dumps(row))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--execute-metadata", action="store_true")
    args = parser.parse_args()
    if not args.out.name.startswith("diagnosis30_"):
        parser.error("require a new diagnosis30_* directory; node28 applicability is recorded")
    args.out.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[6]
    base = root / "codex_coding/results/v0.1.23.3/task2/three_node_recovery"
    original_path = base / "admission28_launch_init_r01/launch.json"
    original = json.loads(original_path.read_text())
    setup = json.loads((base / "admission28_setup_r01/stdout.json").read_text())
    spec = {"hostname": "m03r2n08", "boot_id": "5313e6f8-1712-45dc-9855-4c47358b6c4b",
            "container_id": original["exact_container_id"], "container_started": original["container_started"],
            "image_id": setup["container"]["image_id"]}
    payload = cpu.CONTAINER_SOURCE.replace("m03r2n10", spec["hostname"]).replace(cpu.BOOT, spec["boot_id"])
    code = "import os, json, resource, signal, subprocess, time\nfrom pathlib import Path\n"
    code += "KEYS = " + repr(cpu.KEYS) + "\nSPEC = " + repr(spec) + "\nCONTAINER_SOURCE = " + repr(payload) + "\n"
    code += inspect.getsource(cpu.proc) + "\n" + inspect.getsource(cpu.bounded) + "\n" + REMOTE_MAIN
    compile(code, "readonly_host.py", "exec")
    (args.out / "readonly_host.py").write_text(code)
    (args.out / "source.py").write_bytes(Path(__file__).read_bytes())
    ssh = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ConnectionAttempts=1",
           "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no", "-o", "ServerAliveInterval=3",
           "-o", "ServerAliveCountMax=1", "-o", "Hostname=12.11.3.28", "node28", "python3 /dev/stdin"]
    (args.out / "metadata_command.json").write_text(json.dumps({"argv": ssh, "stdin_source_sha256": hashlib.sha256(code.encode()).hexdigest(), "executed": args.execute_metadata, "gpu_operations": 0}, indent=2)+"\n")
    if not args.execute_metadata:
        return
    result = cpu.bounded(ssh, 20, code.encode())
    stdout = result.pop("stdout")
    (args.out / "stdout.json").write_text(stdout)
    (args.out / "stderr.txt").write_text(result.pop("stderr"))
    (args.out / "metadata_result.json").write_text(json.dumps(result, indent=2)+"\n")
    assert result["returncode"] == 0 and not result["timed_out"] and result["cleanup_proven"]
    data = json.loads(stdout)
    env = data["static_env"]
    branch = env["branch_paths"]
    added = "/opt/dtk/.hyhal/rocm_smi/lib"
    assert branch[added]["is_dir"]
    assert not branch["/opt/hyhal/lib/cmake/rocm_smi"]["is_dir"] or (branch["/opt/hyhal/lib/librocm_smi64.so"]["exists"] and branch["/opt/hyhal/lib/librocm_smi64.so.1"]["exists"])
    inherited = env["env_whitelist"]["LD_LIBRARY_PATH"]
    entries = inherited.split(":")
    assert added not in entries and entries.count("/opt/dtk/opencl/lib") == 1
    entries.insert(entries.index("/opt/dtk/opencl/lib")+1, added)
    ld = ":".join(entries)
    argv = original["remote_argv"].copy()
    argv[argv.index("--out")+1] = str(base / "probe_node28_ldpath_r01")
    index = argv.index(spec["container_id"])
    argv[index:index] = ["-e", "LD_LIBRARY_PATH="+ld]
    launch = original["argv"].copy()
    launch[-1] = shlex.join(argv)
    (args.out / "command.sh").write_text("#!/bin/bash\nset -u\nulimit -c 0\n"+shlex.join(launch)+"\n")
    record = {"applies_to_node": "node28", "executed_gpu": False, "run_name": "probe_node28_ldpath_r01", "argv": launch, "remote_argv": argv,
              "baseline_launch_path": str(original_path), "baseline_launch_sha256": hashlib.sha256(original_path.read_bytes()).hexdigest(),
              "single_runtime_variable": "LD_LIBRARY_PATH", "inherited": inherited, "variant": ld, "added_directory": added,
              "identity": spec, "metadata_input_sha256": hashlib.sha256(stdout.encode()).hexdigest(), "env_sh_sha256": env["env_sh"]["sha256"],
              "source_unchanged": original["init_source_sha256"], "deadline_seconds": 15, "payload_bytes": 0,
              "other_configuration": "same-day original node28 init command, including OMP/MKL1, selected GPU2/BDF, exact container and init supervisor; only unique output run name changes",
              "predictions": {"if_sufficient": "within 15s set_device_exit -> BDF0000:55:00.0 -> HIPfree/admit -> exact cleanup passed", "if_insufficient": "same set_device timeout; no copy/XML; stop and reassess"},
              "parent_conditions": ["fresh guard/protection reviewed", "original child13 exact cleanup and no pending group", "parent alone executes GPU and collects exact run artifacts", "after guard/protection and source SHA check"]}
    (args.out / "launch.json").write_text(json.dumps(record, indent=2)+"\n")
    print(json.dumps({"out": str(args.out), "metadata_passed": True, "command_prepared": True, "gpu_operations": 0, "added_directory": added}))


if __name__ == "__main__":
    main()
