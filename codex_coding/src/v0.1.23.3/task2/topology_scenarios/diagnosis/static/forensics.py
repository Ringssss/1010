"""Read frozen logs and installed communication metadata without GPU operations."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import urllib.request
import xml.etree.ElementTree as ET


def sha(data):
    return hashlib.sha256(data).hexdigest()


def cpus(mask):
    value = int(mask.replace(",", ""), 16)
    return [i for i in range(value.bit_length()) if value & (1 << i)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--container", required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    result = {"gpu_operations": 0, "ranks": [], "installed": {}, "references": {}}
    for rank in range(4):
        path, = args.run.glob(f"rank_{rank}.nccl.*.log")
        raw = path.read_bytes()
        lines = raw.decode().splitlines()
        evidence = [{"line": i + 1, "text": line} for i, line in enumerate(lines)
                    if any(word in line for word in ("RCCL version", "Librccl path", "Plugin Path", "Setting affinity", "Loading topology file", "GPU Direct RDMA", "coll channels", "Init COMPLETE", "issued GDR flush", "Using network", "NET/IB : Using"))]
        channels = {}
        for direction in ("send", "receive"):
            selected = [line for line in lines if f"[{direction}] via NET/" in line]
            channels[direction] = {"connections": len(selected), "gdrdma_connections": sum("/GDRDMA" in line for line in selected)}
        data = json.loads((args.run / f"rank_{rank}.json").read_text())
        timing = []
        for case in data["cases"]:
            flow = case.get("flow")
            if case["case_id"].startswith("network") and flow:
                intervals = flow["chunk_intervals"]
                timing.append({"case": case["case_id"], "role": flow["role"],
                    "elapsed_ms": (flow["receiver_end_ns"] - flow["receiver_start_ns"]) / 1e6,
                    "warmup_ms": (flow["warmup"]["end_ns"] - flow["warmup"]["start_ns"]) / 1e6,
                    "chunks_ms": [(end - start) / 1e6 for start, end in intervals]})
        result["ranks"].append({"rank": rank, "log": str(path), "sha256": sha(raw), "evidence": evidence, "channels": channels, "timing": timing})
    command = """readlink -f /opt/dtk/lib/librccl.so.1
sha256sum /opt/dtk/lib/librccl.so.1 /opt/rccl-rdma-sharp-plugins/lib/librccl-net-shca.so /usr/local/built-in-508-topo-input-tj-default.xml
strings /opt/dtk/lib/librccl.so.1 | grep -E 'NCCL_NET_GDR_LEVEL|NET_GDR_READ|RCCL_FORCE_ENABLE_GDRDMA|NCCL_IGNORE_CPU_AFFINITY|GDRDMA not enabled'
cat /opt/rccl-rdma-sharp-plugins/lib/librccl-net-shca.la
find /opt/rccl-rdma-sharp-plugins /opt/dtk/rccl -maxdepth 4 -type f \\( -name '*.cc' -o -name '*.cpp' \\)
"""
    for node in ("node27", "node29"):
        for label, script in (("metadata", command), ("topology", "cat /usr/local/built-in-508-topo-input-tj-default.xml")):
            argv = ["docker", "exec", args.container, "bash", "-lc", script]
            if node != "node27":
                argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", node, shlex.join(argv)]
            proc = subprocess.run(argv, capture_output=True, timeout=45)
            filename = f"{node}_{label}.txt"
            (args.out / filename).write_bytes(proc.stdout)
            result["installed"][f"{node}_{label}"] = {"file": filename, "sha256": sha(proc.stdout), "returncode": proc.returncode, "stderr": proc.stderr.decode(errors="replace"), "argv": argv}
            if label == "topology" and proc.returncode == 0:
                xml = ET.fromstring(proc.stdout)
                mapped = []
                for cpu in xml.findall("cpu"):
                    for pci in cpu.iter("pci"):
                        if pci.find("gpu") is not None or pci.find("nic") is not None:
                            mapped.append({"bdf": pci.get("busid"), "numaid": cpu.get("numaid"), "affinity_mask": cpu.get("affinity"), "cpus": cpus(cpu.get("affinity")), "gpu": pci.find("gpu").attrib if pci.find("gpu") is not None else None})
                result["installed"][f"{node}_topology"]["mapping"] = mapped
    # These upstream files document semantics, not the exact vendor build.
    base = "https://raw.githubusercontent.com/ROCm/rccl/rocm-6.3.3/"
    for name in ("src/transport/net.cc", "src/graph/paths.cc", "src/graph/topo.cc", "src/init.cc", "src/proxy.cc", "src/include/cpuset.h"):
        try:
            raw = urllib.request.urlopen(base + name, timeout=25).read()
            filename = "reference_" + name.replace("/", "_")
            (args.out / filename).write_bytes(raw)
            result["references"][name] = {"url": base + name, "file": filename, "sha256": sha(raw), "scope": "upstream semantic reference; installed vendor build not source-verified"}
        except Exception as exc:
            result["references"][name] = {"url": base + name, "error": repr(exc)}
    (args.out / "evidence.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"out": str(args.out), "ranks": len(result["ranks"]), "gpu_operations": 0}))


if __name__ == "__main__":
    main()
