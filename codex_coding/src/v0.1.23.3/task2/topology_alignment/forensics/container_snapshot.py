"""Read XML, installed ELF metadata and whitelisted keeper environments."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ALLOW = {"NCCL_TOPO_FILE", "NCCL_TOPO_MAPPING_FILE", "NCCL_CONF_FILE", "NCCL_IB_HCA", "NCCL_NET_PLUGIN", "NCCL_NET_GDR_LEVEL", "NCCL_IGNORE_CPU_AFFINITY", "NCCL_SOCKET_IFNAME", "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "DTK_ROOT", "DTK_HOME", "ROCM_PATH", "HOME", "LD_LIBRARY_PATH"}

def filtered(raw):
    return {k: v for k, v in (item.split("=", 1) for item in raw.split("\0") if "=" in item) if k in ALLOW}

def cmd(argv):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    return {"argv": argv, "returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr}

def file(path, content=False):
    p = Path(path)
    item = {"path": path, "resolved": str(p.resolve()), "exists": p.exists()}
    if p.is_file():
        data = p.read_bytes()
        item.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        if content:
            item["base64"] = base64.b64encode(data).decode()
    return item

out = {"hostname": os.uname().nodename, "env_whitelist": {k: v for k, v in os.environ.items() if k in ALLOW}, "files": [], "processes": [], "commands": {}}
out["namespace_sysfs"] = {"devices": [], "numa": {p.name: (p / "cpulist").read_text().strip() for p in Path("/sys/devices/system/node").glob("node[0-9]*")}}
for p in [*Path("/sys/class/drm").glob("renderD*"), *Path("/sys/class/infiniband").glob("*")]:
    dev = (p / "device").resolve()
    out["namespace_sysfs"]["devices"].append({"class_path": str(p), "resolved": str(dev), "numa_node": (dev / "numa_node").read_text().strip(), "local_cpulist": (dev / "local_cpulist").read_text().strip()})
for p in Path("/proc").glob("[0-9]*"):
    try:
        argv = (p / "cmdline").read_bytes().decode(errors="replace").split("\0")
        if any("fluidckpt.cluster.worker" in a for a in argv):
            status = (p / "status").read_text()
            out["processes"].append({"pid": int(p.name), "argv": argv, "env_whitelist": filtered((p / "environ").read_bytes().decode(errors="replace")), "stat": (p / "stat").read_text(), "status_selected": [l for l in status.splitlines() if l.startswith(("Name:", "Cpus_allowed_list:", "Mems_allowed_list:"))], "mapped_libraries": [l for l in (p / "maps").read_text().splitlines() if "librccl" in l]})
    except (OSError, ProcessLookupError):
        pass
lib = "/opt/dtk/lib/librccl.so.1"
paths = [lib, "/opt/rccl-rdma-sharp-plugins/lib/librccl-net-shca.so", "/usr/local/built-in-508-topo-input-tj-default.xml", "/opt/dtk/rccl/lib/topo_mapping_default.xml", "/etc/nccl.conf", os.path.expanduser("~/.nccl.conf")]
for prefix in ("/usr/local", "/opt/dtk/rccl", "/opt/dtk/lib", "/opt/rccl-rdma-sharp-plugins", "/opt/dtk/etc"):
    for root, directories, names in os.walk(prefix):
        if root.count("/") - prefix.count("/") >= 4:
            directories[:] = []
        for name in names:
            if name.endswith(".xml") and any(t in name.lower() for t in ("topo", "mapping")):
                paths.append(str(Path(root) / name))
for path in sorted(set(paths)):
    out["files"].append(file(path, path.endswith(".xml")))
    if path.endswith("nccl.conf") and Path(path).is_file():
        out.setdefault("conf_whitelist", {})[path] = [l for l in Path(path).read_text().splitlines() if l.split("=", 1)[0].strip() in ALLOW]
symbols = cmd(["nm", "-S", "-C", lib])
terms = ("ncclTopoGetSystem(", "ncclTopoGetXmlFromMappingFile(", "ncclTopoGetXmlFromFile(", "ncclGetEnv(", "ncclInitEnv(")
lines = [l for l in symbols["stdout"].splitlines() if any(t in l for t in terms)]
out["commands"]["symbol_subset"] = {"argv": symbols["argv"], "returncode": symbols["returncode"], "stdout": "\n".join(lines), "stderr": symbols["stderr"]}
for line in lines:
    fields = line.split(maxsplit=3)
    if len(fields) == 4 and fields[2].lower() == "t":
        start, size = int(fields[0], 16), int(fields[1], 16)
        name = fields[3]
        out["commands"][name] = cmd(["objdump", "-d", "-C", f"--start-address={start}", f"--stop-address={start + size}", lib])
data = Path(lib).read_bytes()
out["binary_string_subset"] = [{"offset_hex": hex(m.start()), "text": m.group().decode(errors="replace")} for m in re.finditer(rb"[\x20-\x7e]{4,}", data) if re.search(rb"TOPO|topo_mapping|nccl\.conf|DTK|ROCM_PATH|Loading topology|NCCL_CONF|NCCL_TOPO|%s/.*rccl", m.group())]
out["rodata_points"] = {hex(offset): data[offset:data.index(b"\0", offset)].decode(errors="replace") for offset in (0x152f3, 0x23732, 0x105e7, 0x1914f, 0x166ed, 0x1b2b5)}
print(json.dumps(out, indent=2))
