"""Record the effective environment before importing the frozen GPU probe."""
import hashlib
import json
import os
from pathlib import Path
import resource
import runpy
import sys
import time

HERE = Path(__file__).resolve().parent
CALIBRATION = HERE.parents[1] / "topology_scenarios/calibration"
sys.path.insert(0, str(CALIBRATION))
from inventory import clock_domain, write

# Only communication and device controls are recorded; unrelated secrets are excluded.
EXTRA_KEYS = ("HIP_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "AMD_SERIALIZE_KERNEL",
              "ROCR_VISIBLE_DEVICES", "LD_LIBRARY_PATH", "PYTHONPATH", "ROCM_PATH", "HIP_PATH",
              "HSA_ENABLE_SDMA", "HSA_FORCE_FINE_GRAIN_PCIE",
              "GLOO_SOCKET_IFNAME", "OMP_NUM_THREADS", "MKL_NUM_THREADS")


def environment():
    return {key: value for key, value in sorted(os.environ.items())
            if key.startswith(("NCCL_", "RCCL_", "HIP_", "ROCM_")) or key in EXTRA_KEYS}


def loaded_libraries():
    paths = set()
    for line in Path("/proc/self/maps").read_text().splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and fields[5].startswith("/"):
            path = fields[5]
            if any(name in path.lower() for name in ("rccl", "nccl", "shca", "libtorch", "libamdhip")):
                paths.add(path)
    result = []
    for name in sorted(paths):
        path = Path(name)
        row = {"path": name}
        try:
            sha = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(8 << 20), b""):
                    sha.update(chunk)
            row.update(sha256=sha.hexdigest(), bytes=path.stat().st_size)
        except OSError as exc:
            row["error"] = repr(exc)
        result.append(row)
    return result


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    rank = int(sys.argv[sys.argv.index("--rank") + 1])
    out = Path(sys.argv[sys.argv.index("--out") + 1])
    record = {"rank": rank, "pid": os.getpid(), "clock_domain": clock_domain(),
              "captured_monotonic_ns": time.monotonic_ns(), "effective_environment": environment(),
              "torch_imported": "torch" in sys.modules, "pre_torch_import": True,
              "cpu_affinity": sorted(os.sched_getaffinity(0)),
              "core_dump_limit": list(resource.getrlimit(resource.RLIMIT_CORE))}
    if record["torch_imported"]:
        raise RuntimeError("environment evidence was captured after Torch import")
    topo = os.environ.get("NCCL_TOPO_FILE")
    if topo:
        path = Path(topo)
        record["requested_topology_input"] = {"path": topo,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
    record["vendor_candidate_files"] = []
    for name in ("/rccl/lib/topo_mapping_default.xml",
                 "/usr/local/built-in-508-topo-input-tj-default.xml"):
        path = Path(name)
        if path.exists():
            record["vendor_candidate_files"].append({"path": name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size,
                "scope": "candidate file presence only; not proof it was loaded"})
    write(out / f"rank_{rank}.environment.json", record)
    try:
        runpy.run_path(str(CALIBRATION / "probe_rank.py"), run_name="__main__")
    finally:
        write(out / f"rank_{rank}.libraries.json", {"rank": rank, "pid": os.getpid(),
            "libraries": loaded_libraries(), "clock_domain": clock_domain()})


if __name__ == "__main__":
    main()
