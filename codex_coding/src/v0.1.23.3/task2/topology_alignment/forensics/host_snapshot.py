"""Read host sysfs and sanitized Docker provenance; never use a GPU API."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ALLOW = {"NCCL_TOPO_FILE", "NCCL_TOPO_MAPPING_FILE", "NCCL_CONF_FILE", "NCCL_IB_HCA", "NCCL_NET_PLUGIN", "NCCL_NET_GDR_LEVEL", "NCCL_IGNORE_CPU_AFFINITY", "NCCL_SOCKET_IFNAME", "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "DTK_ROOT", "DTK_HOME", "ROCM_PATH", "HOME", "LD_LIBRARY_PATH"}

def filtered(items):
    return {k: v for k, v in (item.split("=", 1) for item in items or [] if "=" in item) if k in ALLOW}

def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError as exc:
        return {"error": str(exc)}

def chain(path):
    actual = Path(path).resolve()
    result = []
    for p in (actual, *actual.parents):
        if re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]", p.name):
            result.append({"bdf": p.name, "sysfs": str(p), **{name: read(p / name) for name in ("numa_node", "local_cpulist", "vendor", "device", "class", "current_link_speed", "current_link_width", "max_link_speed", "max_link_width")}})
    return result

def inspect(argv):
    return json.loads(subprocess.check_output(argv))[0]

container = sys.argv[1]
ci = inspect(["docker", "inspect", container])
ii = inspect(["docker", "image", "inspect", ci["Image"]])
image_env, container_env = filtered(ii["Config"].get("Env")), filtered(ci["Config"].get("Env"))
out = {"hostname": os.uname().nodename, "boot_id": read("/proc/sys/kernel/random/boot_id"), "container": {"name": container, "id": ci["Id"], "image_id": ci["Image"], "created": ci["Created"], "started": ci["State"]["StartedAt"], "pid": ci["State"]["Pid"], "running": ci["State"]["Running"], "env_whitelist": container_env, "mounts": [{k: m.get(k) for k in ("Type", "Source", "Destination", "RW")} for m in ci["Mounts"]]}, "image": {"id": ii["Id"], "repo_tags": ii.get("RepoTags"), "repo_digests": ii.get("RepoDigests"), "created": ii.get("Created"), "env_whitelist": image_env}, "env_comparison": {k: {"image": image_env.get(k), "container": container_env.get(k), "same": image_env.get(k) == container_env.get(k)} for k in sorted(ALLOW) if k in image_env or k in container_env}, "numa": {}, "drm": [], "kfd": [], "hca": []}
for p in sorted(Path("/sys/devices/system/node").glob("node[0-9]*")):
    out["numa"][p.name] = {"cpulist": read(p / "cpulist"), "distance": read(p / "distance")}
for p in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
    if re.fullmatch("card[0-9]+", p.name):
        out["drm"].append({"card": p.name, "pci_chain": chain(p / "device")})
for p in sorted(Path("/sys/class/kfd/kfd/topology/nodes").glob("*")):
    properties = dict(line.split(maxsplit=1) for line in read(p / "properties").splitlines())
    if properties.get("simd_count") != "0":
        minor = properties.get("drm_render_minor")
        out["kfd"].append({"node": p.name, "gpu_id": read(p / "gpu_id"), "properties": properties, "pci_chain": chain(f"/sys/class/drm/renderD{minor}/device")})
for p in sorted(Path("/sys/class/infiniband").glob("*")):
    out["hca"].append({"name": p.name, "pci_chain": chain(p / "device"), "ports": {q.name: {key: read(q / key) for key in ("rate", "state", "phys_state", "link_layer")} for q in (p / "ports").glob("*")}})
print(json.dumps(out, indent=2))
