"""Read node topology and occupancy before launching an isolated cluster experiment."""
import argparse
import json
from pathlib import Path
import resource
import subprocess
import sys
import importlib.metadata
import mmap

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.topology.discover import discover
from fluidckpt.memory.numa import mbind, placement

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
parser = argparse.ArgumentParser(); parser.add_argument("--out", required=True)
args = parser.parse_args()
raw = subprocess.check_output(["hy-smi", "--showmeminfo", "vram", "--json"], text=True)
topology = discover()
with mmap.mmap(-1, 4096, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS) as page:
    numa = topology["gpus"][0]["numa_node"]
    mbind(page, numa, move=False); page[0] = 1
    bound = placement(page, samples=1)
ports, occupied_ports = [], []
for name in ("tcp", "tcp6"):
    for line in Path("/proc/net/"+name).read_text().splitlines()[1:]:
        fields = line.split()
        occupied_ports.append(int(fields[1].split(":")[1], 16))
        if fields[3] == "0A": ports.append(int(fields[1].split(":")[1], 16))
result = {"topology": topology, "gpu_memory": json.loads(raw[raw.index("{"):]),
          "numa_binding_probe": bound, "listening_ports": ports,
          "occupied_tcp_ports": sorted(set(occupied_ports)),
          "software": {name: importlib.metadata.version(name) for name in ("torch", "sglang", "scipy")}}
Path(args.out).write_text(json.dumps(result, indent=2))
print(json.dumps({"fingerprint": result["topology"]["fingerprint"], "gpu_memory": result["gpu_memory"]}))
