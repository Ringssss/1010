"""One focused owner/free refresh of authorized node28/30 candidates."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import threading
import time

def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError as exc:
        return {"error": repr(exc)}

def proc(pid):
    row = {"pid": int(pid)}
    stat = read(f"/proc/{pid}/stat")
    if isinstance(stat, str):
        fields = stat.rsplit(") ", 1)[1].split()
        row.update(state=fields[0], start_ticks=fields[19], comm=read(f"/proc/{pid}/comm"), cgroup=read(f"/proc/{pid}/cgroup"))
    try:
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0")
        row["selected_args"] = {a: argv[i + 1] for i, a in enumerate(argv[:-1]) if a in ("--gpu", "--gpu-id", "--node", "--port-base")}
        row["is_fluid_worker"] = any("fluidckpt.cluster.worker" in a for a in argv)
        row["is_vllm"] = any("vllm" in a.lower() for a in argv)
        keys = {"HIP_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES"}
        env = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        row["gpu_visibility_only"] = {k.decode(): v.decode(errors="replace") for item in env if b"=" in item
                                      for k, v in [item.split(b"=", 1)] if k.decode(errors="replace") in keys}
    except OSError as exc:
        row["process_visibility_error"] = repr(exc)
    return row

def main():
    node, source_sha = sys.argv[1:3]
    if node not in ("node28", "node30") or os.uname().nodename != {"node28": "m03r2n08", "node30": "m03r2n10"}[node]:
        raise SystemExit("owner metadata authorized only node28/30")
    def deadline():
        time.sleep(25)
        os._exit(124)
    threading.Thread(target=deadline, daemon=True).start()
    out = {"node": node, "hostname": os.uname().nodename, "boot_id": read("/proc/sys/kernel/random/boot_id"),
           "sampled_utc": datetime.now(timezone.utc).isoformat(), "source_sha256": source_sha,
           "gpus": [], "kfd_processes": [], "process_summary": [], "gpu_calls": 0,
           "remote_writes": 0, "signals": [], "node27_driver_reads": 0, "node29_accesses": 0,
           "allowed_driver_keys": ["mem_info_vram_total", "mem_info_vram_used", "vram_[0-9]+", "pasid"],
           "runtime_admission": False}
    wanted = [(0, "0000:09:00.0"), (1, "0000:36:00.0")] if node == "node28" else [(4, "0000:85:00.0"), (5, "0000:b5:00.0")]
    for ordinal, bdf in wanted:
        p = Path("/sys/bus/pci/devices") / bdf
        total, used = read(p / "mem_info_vram_total"), read(p / "mem_info_vram_used")
        out["gpus"].append({"historical_ordinal": ordinal, "bdf": bdf, "numa": read(p / "numa_node"),
                            "sysfs_total_bytes": total, "sysfs_used_bytes": used,
                            "sysfs_total_minus_used_bytes": int(total) - int(used) if isinstance(total, str) and isinstance(used, str) else None,
                            "free_is_gpu_health_or_task_authorization": False})
    root = Path("/sys/class/kfd/kfd/proc")
    if root.exists():
        entries = sorted((p for p in root.iterdir() if p.name.isdigit()), key=lambda p: int(p.name))
        out["kfd_proc_truncated"] = len(entries) > 128
        for p in entries[:128]:
            row = proc(p.name)
            row["kfd_numeric_files"] = {q.name: read(q) for q in p.iterdir() if re.fullmatch(r"vram_[0-9]+|pasid", q.name)}
            out["kfd_processes"].append(row)
    out["kfd_zero_does_not_prove_no_owners"] = True
    for p in sorted((p for p in Path("/proc").iterdir() if p.name.isdigit()), key=lambda p: int(p.name))[:4096]:
        comm = read(p / "comm")
        if isinstance(comm, str) and any(s in comm.lower() for s in ("python", "vllm", "sglang")):
            out["process_summary"].append(proc(p.name))
    out["ended_utc"] = datetime.now(timezone.utc).isoformat()
    print(json.dumps(out, indent=2), flush=True)

if __name__ == "__main__":
    main()
