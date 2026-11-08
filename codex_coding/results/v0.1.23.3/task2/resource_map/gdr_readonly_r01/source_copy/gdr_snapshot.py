"""Read deployed ELF branches and kernel/device prerequisites; never load HIP."""
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError as exc:
        return {"error": str(exc)}


def command(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=90)
    return {"argv": argv, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def main():
    result = {"read_only": True, "gpu_operations": 0, "uid": os.getuid(), "gid": os.getgid(),
              "kernel_release": os.uname().release, "modules": read("/proc/modules"),
              "memlock_limit": read("/proc/self/limits"), "cpuinfo_first": read("/proc/cpuinfo")[:3500],
              "env": {k: v for k, v in os.environ.items() if k.startswith(("NCCL_", "RCCL_")) or k in ("HSA_FORCE_FINE_GRAIN_PCIE", "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "ROCM_PATH")},
              "devices": [], "hca": [], "elf": [], "kernel_symbols": [], "module_parameters": {}}
    for path in sorted(glob.glob("/dev/kfd") + glob.glob("/dev/dri/renderD*") + glob.glob("/dev/infiniband/*") + glob.glob("/dev/*peer*")):
        info = os.stat(path)
        result["devices"].append({"path": path, "mode": stat.filemode(info.st_mode), "uid": info.st_uid,
                                  "gid": info.st_gid, "major": os.major(info.st_rdev), "minor": os.minor(info.st_rdev),
                                  "readable": os.access(path, os.R_OK), "writable": os.access(path, os.W_OK)})
    for name in ("hygon", "amdgpu", "shca", "ib_core", "ib_uverbs", "nvidia_peermem", "amd_peer_mem", "hygon_peer_memory", "hsa_peer_memory", "hip_peer_memory", "rck_peer_mem"):
        directory = Path("/sys/module") / name
        result["module_parameters"][name] = {"exists": directory.exists(), "parameters": {p.name: read(p) for p in (directory / "parameters").glob("*")}}
    for path in sorted(Path("/sys/class/infiniband").glob("*")):
        result["hca"].append({"name": path.name, "device": str((path / "device").resolve()),
                              "node_guid": read(path / "node_guid"), "sys_image_guid": read(path / "sys_image_guid"),
                              "firmware": read(path / "fw_ver"), "driver": str((path / "device/driver").resolve())})
    try:
        with Path("/proc/kallsyms").open() as handle:
            result["kernel_symbols"] = [line.strip() for line in handle if re.search(r"ib_register_peer_memory_client|ib_unregister_peer_memory_client|ib_umem_dmabuf|dma_buf_(export|attach)|peer.*memory|peer_mem", line)][:160]
    except OSError as exc:
        result["kernel_symbols"] = {"error": str(exc)}
    names = {"rccl": "/opt/dtk/lib/librccl.so.1", "plugin": "/opt/rccl-rdma-sharp-plugins/lib/librccl-net-shca.so"}
    patterns = {"rccl": r"ncclTopoFill(?:Gpu|Net)\(|ncclGpuGdrSupport\(|ncclTopoCheckGdr\(|ncclIbGdrSupport\(|ncclIbDmaBufSupport\(",
                "plugin": r"ncclIbGdrSupport|ncclIbGetProperties|ncclIbDmaBufSupport|ncclIbInit"}
    for label, name in names.items():
        path = Path(name)
        if not path.exists():
            continue
        sha = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(8 << 20), b""):
                sha.update(chunk)
        nm = command(["nm", "-S", "-C", str(path)])
        symbols = [line for line in nm["stdout"].splitlines() if re.search(patterns[label], line)]
        record = {"label": label, "path": name, "resolved": str(path.resolve()), "sha256": sha.hexdigest(),
                  "symbols": symbols, "nm_returncode": nm["returncode"], "nm_stderr": nm["stderr"], "disassembly": []}
        for line in symbols:
            match = re.match(r"([0-9a-f]+) ([0-9a-f]+) [A-Za-z] (.+)", line)
            if match and "clone" not in match[3]:
                start, length = int(match[1], 16), int(match[2], 16)
                if length <= 18000:
                    asm = command(["objdump", "-d", "-C", f"--start-address={start}", f"--stop-address={start+length}", str(path)])
                    asm["symbol"] = match[3]
                    record["disassembly"].append(asm)
        strings = command(["strings", "-t", "x", str(path)])
        record["string_subset"] = [line for line in strings["stdout"].splitlines() if re.search(r"GPU Direct RDMA|[gG][dD][rR]Support|DmaBuf|DMABUF|dma.?buf|peer.?mem|/sys/kernel/mm/memory_peers|HSA_FORCE_FINE|PointerGetAttributes", line)][:200]
        dynamic = command(["nm", "-D", "-C", str(path)])
        record["dynamic_subset"] = [line for line in dynamic["stdout"].splitlines() if re.search(r"peer|dma.?buf|hipPointer|hipDevice|hipGetDevice|reg_mr", line, re.I)]
        result["elf"].append(record)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
