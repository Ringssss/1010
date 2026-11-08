"""Correct accelerator-class selection with a bounded static-only follow-up."""
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

def chain(path):
    actual = Path(path).resolve()
    return [{"bdf": p.name, "sysfs": str(p), **{k: read(p / k) for k in
            ("numa_node", "local_cpulist", "vendor", "device", "class")}}
            for p in (actual, *actual.parents)
            if re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]", p.name)]

def main():
    node, source_sha = sys.argv[1:3]
    if os.uname().nodename != {"node27": "m03r2n07", "node28": "m03r2n08", "node30": "m03r2n10"}.get(node):
        raise SystemExit("host identity mismatch")
    def deadline():
        time.sleep(15)
        os._exit(124)
    threading.Thread(target=deadline, daemon=True).start()
    out = {"node": node, "hostname": os.uname().nodename,
           "boot_id": read("/proc/sys/kernel/random/boot_id"),
           "sampled_utc": datetime.now(timezone.utc).isoformat(), "source_sha256": source_sha,
           "classification_correction": "Hygon DCU is PCI class 0x0b4000, vendor 0x1d94, device 0x6320; class03 is the management VGA",
           "gpus": [], "drm_symlinks": [], "gpu_calls": 0, "driver_property_reads": 0,
           "signals": [], "remote_writes": 0, "node29_accesses": 0, "runtime_admission": False}
    for p in sorted(Path("/sys/bus/pci/devices").iterdir()):
        if read(p / "class") == "0x0b4000" and read(p / "vendor") == "0x1d94" and read(p / "device") == "0x6320":
            out["gpus"].append({"bdf": p.name, "pci_chain": chain(p),
                                "driver_symlink": str((p / "driver").resolve()), "free_hbm_bytes": None})
    for p in sorted(Path("/sys/class/drm").glob("*")):
        if re.fullmatch(r"card[0-9]+|renderD[0-9]+", p.name):
            out["drm_symlinks"].append({"name": p.name, "device": str((p / "device").resolve())})
    out["device_nodes"] = {p: Path(p).exists() for p in ("/dev/kfd", "/dev/mkfd", "/dev/dri", "/dev/infiniband", "/opt/hyhal", "/opt/dtk")}
    print(json.dumps(out, indent=2), flush=True)

if __name__ == "__main__":
    main()
