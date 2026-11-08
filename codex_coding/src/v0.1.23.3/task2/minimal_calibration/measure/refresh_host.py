"""Reuse the frozen metadata guard and add CPU/NUMA/storage observations."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import signal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--guard-source", required=True)
    parser.add_argument("--spec", required=True)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("metadata 25s deadline")))
    signal.alarm(25)
    spec = json.loads(Path(args.spec).read_text())
    source = Path(args.guard_source)
    if hashlib.sha256(source.read_bytes()).hexdigest() != spec["host_wrapper_sha256"]:
        raise ValueError("frozen guard source SHA differs")
    if os.uname().nodename != spec["hostname"] or Path("/proc/sys/kernel/random/boot_id").read_text().strip() != spec["boot_id"]:
        raise ValueError("host identity differs")
    module_spec = importlib.util.spec_from_file_location("frozen_metadata_guard", source)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    result = module.guard(spec)
    result["container_identity_checks"] = {
        "id": result["container"].get("id") == spec.get("expected_container_id"),
        "started": result["container"].get("State", {}).get("StartedAt") == spec.get("expected_container_started"),
        "running": result["container"].get("State", {}).get("Running") is True,
    }
    result["cpu"] = {"loadavg": module.read("/proc/loadavg"), "stat": module.read("/proc/stat"),
                     "online": module.read("/sys/devices/system/cpu/online")}
    result["numa"] = {node.name: {"meminfo": module.read(node / "meminfo"), "cpulist": module.read(node / "cpulist")}
                      for node in sorted(Path("/sys/devices/system/node").glob("node[0-9]*"))}
    result["all_gpu_sysfs"] = {
        path.name: {key: module.read(path / key) for key in ("mem_info_vram_total", "mem_info_vram_used", "numa_node")}
        for path in sorted(Path("/sys/bus/pci/devices").iterdir()) if (path / "mem_info_vram_total").exists()
    }
    result["kfd_gpu_nodes"] = {
        path.name: {"gpu_id": module.read(path / "gpu_id"), "properties": module.read(path / "properties")}
        for path in sorted(Path("/sys/class/kfd/kfd/topology/nodes").iterdir())
    }
    result["storage"] = {}
    for path in ("/", "/tmp", "/dev/shm", "/var/lib/docker"):
        stat = os.statvfs(path)
        result["storage"][path] = {"available_bytes": stat.f_bavail * stat.f_frsize, "free_bytes": stat.f_bfree * stat.f_frsize}
    result["sampled_at_utc"] = datetime.now(timezone.utc).isoformat()
    result["fresh_hip_free_bytes"] = None
    result["formal_performance_ready"] = False
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
