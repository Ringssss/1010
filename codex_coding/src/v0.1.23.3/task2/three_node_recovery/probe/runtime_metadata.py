"""Bounded CPU-only container runtime metadata; never load Torch/HIP or open GPU devices."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import shlex
import socket
import stat

ROOT = Path(__file__).resolve().parents[6]
RESULT_ROOT = ROOT / "codex_coding/results/v0.1.23.3/task2/three_node_recovery"
ENV = ("PATH", "LD_LIBRARY_PATH", "ROCM_PATH", "LD_PRELOAD", "HSA_ENABLE_SDMA", "HSA_FORCE_FINE_GRAIN_PCIE",
       "HSA_TOOLS_LIB", "HIP_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "NCCL_TOPO_FILE")


def static_assignments(raw):
    result = []
    for line in raw.decode(errors="replace").splitlines():
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError:
            continue
        for token in tokens:
            name, separator, value = token.partition("=")
            if separator and name in ENV:
                result.append(name + "=" + value)
    return result[:80]


def text(path, limit=32768):
    try:
        raw = Path(path).read_bytes()
        return raw[:limit].decode(errors="replace") + ("\n[truncated]" if len(raw) > limit else "")
    except OSError as exc:
        return {"error": repr(exc)}


def metadata(path):
    path = Path(path)
    try:
        row = path.stat()
        return {"path": str(path), "resolved": str(path.resolve()), "uid": row.st_uid, "gid": row.st_gid,
                "mode": oct(stat.S_IMODE(row.st_mode)), "char_device": stat.S_ISCHR(row.st_mode),
                "major_minor": [os.major(row.st_rdev), os.minor(row.st_rdev)] if stat.S_ISCHR(row.st_mode) else None,
                "read_access": os.access(path, os.R_OK), "write_access": os.access(path, os.W_OK),
                "execute_access": os.access(path, os.X_OK)}
    except OSError as exc:
        return {"path": str(path), "error": repr(exc)}


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("readonly metadata 8s deadline")))
    signal.alarm(8)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-hostname", required=True)
    parser.add_argument("--expected-boot", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out = args.out.resolve()
    if args.out.parent != RESULT_ROOT or not args.out.name.startswith("probe_runtime_metadata_"):
        parser.error("require a new probe_runtime_metadata_* directory")
    current_boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if socket.gethostname() != args.expected_hostname or current_boot != args.expected_boot:
        parser.error("container host identity mismatch")
    args.out.mkdir(parents=True, exist_ok=False)
    paths = ["/dev/kfd", "/dev/mkfd", "/dev/dri", "/dev/infiniband", "/dev/shm", "/opt/hyhal",
             "/var/log", "/var/log/hylog", "/opt/dtk/env.sh"]
    paths += [str(p) for p in sorted(Path("/dev/dri").glob("renderD*"))[:16]]
    status = Path("/proc/self/status").read_text()
    selected_status = [line for line in status.splitlines() if line.startswith(("Uid:", "Gid:", "Groups:", "Cap", "NoNewPrivs:", "Seccomp", "Threads:", "Mems_allowed_list:", "Cpus_allowed_list:"))]
    env_path = Path("/opt/dtk/env.sh")
    env_raw = env_path.read_bytes() if env_path.exists() else None
    env_lines = []
    if env_raw:
        if len(env_raw) > 131072:
            raise ValueError("DTK env script exceeds 128KiB bound")
        env_lines = static_assignments(env_raw)
    mount_lines = [line for line in Path("/proc/self/mountinfo").read_text().splitlines()
                   if any(key in line for key in (" /dev/", " /opt/hyhal ", " /dev/shm ", str(ROOT)))]
    result = {"hostname": socket.gethostname(), "boot_id": current_boot, "uid": os.getuid(), "gid": os.getgid(),
              "groups": os.getgroups(), "pid_namespace": os.readlink("/proc/self/ns/pid"),
              "ipc_namespace": os.readlink("/proc/self/ns/ipc"), "effective_env_whitelist": {k: os.environ.get(k) for k in ENV},
              "process_status": selected_status, "limits": text("/proc/self/limits"),
              "path_metadata": [metadata(p) for p in paths], "mountinfo_selected": mount_lines,
              "dtk_env_script": {"sha256": hashlib.sha256(env_raw).hexdigest() if env_raw else None,
                                 "selected_assignment_lines": env_lines, "executed": False},
              "gpu_operations": 0, "device_open_calls": 0, "torch_imported": False, "library_loads": 0,
              "scope": "container UID/caps/seccomp/device metadata/env/log-directory evidence; no runtime health claim"}
    (args.out / "metadata.json").write_text(json.dumps(result, indent=2) + "\n")
    (args.out / "source.py").write_bytes(Path(__file__).read_bytes())
    print(json.dumps({"out": str(args.out), "gpu_operations": 0}))


if __name__ == "__main__":
    main()
