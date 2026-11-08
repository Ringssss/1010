"""Bounded mount and static HIP dependency evidence; never load GPU libraries."""
import argparse
import datetime
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = "/home/zhujianian/zhujianian/fluidckpt_zjn"
MODEL = "/data/node28/shared/zhujianian/Qwen3.6-35B-A3B"
PATHS = [ROOT, "/home/zhujianian", "/data/node28/shared/zhujianian",
         MODEL + "/config.json", "/opt/dtk", "/opt/dtk/lib", "/opt/dtk/hip/lib",
         "/opt/hyhal", "/opt/hyhal/lib", "/dev/kfd", "/dev/dri/renderD128"]


def run(argv, seconds=2):
    start = time.monotonic()
    try:
        p = subprocess.run(["timeout", str(seconds) + "s", *argv],
                           capture_output=True, text=True, timeout=seconds + 1)
        return {"argv": argv, "rc": p.returncode,
                "stdout": p.stdout[:24000], "stderr": p.stderr[:2000],
                "elapsed_s": round(time.monotonic() - start, 6)}
    except subprocess.TimeoutExpired:
        return {"argv": argv, "timeout": True,
                "elapsed_s": round(time.monotonic() - start, 6)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["host", "container"], required=True)
    args = parser.parse_args()
    mounts = Path("/proc/self/mountinfo").read_text().splitlines()
    relevant = [s for s in mounts if s.split()[4] in
                {"/", "/home/zhujianian", "/data/node28", "/data/node28/shared/zhujianian", "/opt/hyhal"}]
    result = {"role": args.role, "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "pid": os.getpid(), "scope": "read-only; no dlopen, HIP, SMI, model weights, network probes or engine health",
              "mountinfo": relevant, "paths": {}, "static_commands": {}}
    for path in PATHS:
        result["paths"][path] = {"stat": run(["stat", "-Lc", "%F %s %d:%i", path]),
                                "realpath": run(["readlink", "-f", path]),
                                "mount": run(["findmnt", "-T", path, "-n", "-o", "TARGET,SOURCE,FSTYPE,OPTIONS"])}
    result["model_config_small_read"] = run(["head", "-c", "64", MODEL + "/config.json"])
    if args.role == "host":
        result["static_commands"]["container_mounts"] = run([
            "docker", "inspect", "--format", "{{json .Mounts}}", "zjn-fluid-basic-1bce3897a2075b"], 3)
        result["static_commands"]["ssh_alias"] = run([
            "ssh", "-G", "node29"], 2)
        alias = result["static_commands"]["ssh_alias"]
        alias["stdout"] = "\n".join(s for s in alias.get("stdout", "").splitlines()
                                    if s.startswith(("hostname ", "port ", "user ", "proxycommand ", "proxyjump ")))
        for ip in ["10.211.3.29", "12.11.3.29", "12.11.3.28"]:
            result["static_commands"]["route_" + ip] = run(["ip", "route", "get", ip])
    else:
        keys = ["PATH", "LD_LIBRARY_PATH", "ROCM_PATH", "HIP_PATH", "HYHAL_PATH", "DTKROOT",
                "HIP_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "NCCL_TOPO_FILE"]
        result["environment_whitelist"] = {key: os.environ.get(key) for key in keys}
        cache = run(["ldconfig", "-p"])
        cache["stdout"] = "\n".join(s for s in cache.get("stdout", "").splitlines()
                                    if any(x in s for x in ["galaxyhip", "amdhip64", "hsakmt", "hsa-runtime"]))
        result["static_commands"]["loader_cache"] = cache
        libs = [s.split("=>", 1)[1].strip() for s in cache.get("stdout", "").splitlines() if "=>" in s]
        libs += ["/opt/dtk/hip/lib/libamdhip64.so", "/opt/dtk/hip/lib/libgalaxyhip.so.5",
                 "/opt/hyhal/lib/libhsa-runtime64.so.1", "/opt/hyhal/lib/libhsakmt.so.1"]
        result["libraries"] = {}
        for library in sorted(set(libs)):
            row = {"stat": run(["stat", "-Lc", "%F %s %d:%i", library]),
                   "link": run(["readlink", library]), "realpath": run(["readlink", "-f", library]),
                   "mount": run(["findmnt", "-T", library, "-n", "-o", "TARGET,SOURCE,FSTYPE"])}
            if row["stat"].get("rc") == 0:
                elf = run(["readelf", "-d", library])
                elf["stdout"] = "\n".join(s for s in elf.get("stdout", "").splitlines()
                                         if any(x in s for x in ["NEEDED", "RPATH", "RUNPATH", "SONAME"]))
                row["readelf_dynamic"] = elf
            result["libraries"][library] = row
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
