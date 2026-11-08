"""Join HIP logical IDs, PCI BDFs, DRM devices and NUMA placement without ordinal guesses."""
import ctypes
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time


def read(path, default=None):
    try:
        return Path(path).read_text().strip()
    except (FileNotFoundError, PermissionError):
        return default


def memory_node(node):
    text = read(f"/sys/devices/system/node/node{node}/meminfo", "")
    return {key: int(value) * 1024 for key, value in re.findall(r"(\w+):\s+(\d+) kB", text)}


def pci_info(bdf):
    path = Path("/sys/bus/pci/devices") / bdf
    return {"bdf": bdf, "numa_node": int(read(path / "numa_node", "-1")),
            "ancestors": [p.name for p in path.resolve().parents
                          if re.fullmatch(r"[\da-f]{4}:[\da-f]{2}:[\da-f]{2}\.\d", p.name)],
            "current_link_speed": read(path / "current_link_speed"),
            "current_link_width": read(path / "current_link_width")}


def discover():
    lib = ctypes.CDLL(ctypes.util.find_library("amdhip64") or "libamdhip64.so")
    def checked(rc):
        if rc:
            raise RuntimeError(f"HIP topology query returned {rc}")
    checked(lib.hipInit(0))
    count = ctypes.c_int()
    checked(lib.hipGetDeviceCount(ctypes.byref(count)))
    smi_bus = subprocess.check_output(["hy-smi", "--showbus", "--json"], text=True)
    smi = json.loads(smi_bus[smi_bus.index("{"):])
    smi_topo = subprocess.check_output(["hy-smi", "--showtopo"], text=True)
    numa = {int(g): int(n) for g, n in re.findall(r"HCU\[(\d+)\].*Numa Node (\d+)", smi_topo)}
    bus_to_smi = {}
    for key, value in smi.items():
        match = re.search(r"[\da-fA-F]{4}:[\da-fA-F]{2}:[\da-fA-F]{2}\.\d", value["PCI Bus"])
        bus_to_smi[match[0].lower()] = int(key.removeprefix("card"))
    devices = []
    for gpu in range(count.value):
        buf = ctypes.create_string_buffer(32)
        checked(lib.hipDeviceGetPCIBusId(buf, len(buf), gpu))
        bdf = buf.value.decode().lower()
        bdf = f"{int(bdf.split(':')[0], 16):04x}:" + ":".join(bdf.split(":")[1:])
        info = pci_info(bdf)
        if bdf not in bus_to_smi:
            raise RuntimeError(f"HIP GPU {gpu}/{bdf} has no management-tool identity")
        ordinal = bus_to_smi[bdf]
        affinity = numa.get(ordinal)
        if affinity is None or (info["numa_node"] >= 0 and info["numa_node"] != affinity):
            raise RuntimeError(f"conflicting NUMA identities for {bdf}: {info}, SMI={affinity}")
        drm = [p.name for p in Path("/sys/class/drm").glob("card[0-9]*")
               if (p / "device").resolve().name == bdf and "-" not in p.name]
        devices.append(dict(info, logical_gpu=gpu, smi_gpu=ordinal, drm=drm,
                            numa_node=affinity, identity=f"{socket.gethostname()}/{bdf}"))
    nodes = []
    for path in sorted(Path("/sys/devices/system/node").glob("node[0-9]*")):
        n = int(path.name[4:])
        cpus = [p for p in path.glob("cpu[0-9]*") if p.name[3:].isdigit()]
        packages = sorted({int(read(p / "topology/physical_package_id", "-1")) for p in cpus})
        nodes.append({"node": n, "cpulist": read(path / "cpulist"),
                      "reported_package_ids": packages,
                      "distance": read(path / "distance"), "memory_bytes": memory_node(n)})
    io = []
    for subsystem in ("nvme", "infiniband", "net"):
        for p in Path("/sys/class", subsystem).glob("*"):
            dev = (p / "device").resolve()
            if re.fullmatch(r"[\da-f]{4}:[\da-f]{2}:[\da-f]{2}\.\d", dev.name):
                io.append(dict(pci_info(dev.name), subsystem=subsystem, name=p.name))
    stable = {"host": socket.gethostname(), "gpus": devices,
              "nodes": [{k: v for k, v in row.items() if k != "memory_bytes"} for row in nodes],
              "io": io}
    return dict(stable, nodes=nodes, timestamp=time.time(), schema_version=1,
                visibility={k: os.environ.get(k) for k in ("HIP_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES")},
                fingerprint=hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest(),
                sources={"smi_bus": smi, "smi_topology": smi_topo})
