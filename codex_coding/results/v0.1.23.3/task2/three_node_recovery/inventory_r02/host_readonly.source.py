"""Bounded sysfs/proc/Docker inventory; never initialize a GPU runtime."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

ALLOW = {"NCCL_TOPO_FILE", "NCCL_TOPO_MAPPING_FILE", "NCCL_CONF_FILE", "NCCL_IB_HCA", "NCCL_NET_PLUGIN", "NCCL_NET_GDR_LEVEL", "NCCL_IGNORE_CPU_AFFINITY", "NCCL_SOCKET_IFNAME", "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "DTK_ROOT", "DTK_HOME", "ROCM_PATH", "LD_LIBRARY_PATH", "PATH", "PYTHONPATH"}

def now():
    return datetime.now(timezone.utc).isoformat()

def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError as exc:
        return {"error": repr(exc)}

def listing(path):
    try:
        return sorted(Path(path).iterdir(), key=lambda p: p.name)
    except OSError as exc:
        errors.append({"path": str(path), "error": repr(exc)})
        return []

def filtered(items):
    return {k: v for k, v in (item.split("=", 1) for item in items or [] if "=" in item) if k in ALLOW}

def chain(path):
    actual = Path(path).resolve()
    return [{"bdf": p.name, "sysfs": str(p), **{name: read(p / name) for name in ("numa_node", "local_cpulist", "vendor", "device", "class", "current_link_speed", "current_link_width", "max_link_speed", "max_link_width")}} for p in (actual, *actual.parents) if re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]", p.name)]

def command(argv, timeout=3, stdin=None):
    start = time.monotonic()
    row = {"argv": argv, "started_utc": now(), "timeout_s": timeout}
    try:
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout)
        row.update(returncode=p.returncode, stdout=p.stdout, stderr=p.stderr)
    except (OSError, subprocess.TimeoutExpired) as exc:
        row.update(returncode=None, error=repr(exc), stdout="", stderr="")
    row.update(ended_utc=now(), elapsed_s=time.monotonic()-start)
    return row

def cgroups(pid):
    raw = read(f"/proc/{pid}/cgroup")
    out = {"proc_cgroup": raw}
    if isinstance(raw, str):
        v2 = next((line.split(":", 2)[2] for line in raw.splitlines() if line.startswith("0::")), None)
        if v2:
            root = Path("/sys/fs/cgroup") / v2.lstrip("/")
            out["files"] = {name: read(root / name) for name in ("cpu.max", "cpu.stat", "cpuset.cpus.effective", "cpuset.mems.effective", "memory.current", "memory.max", "memory.events", "pids.current", "pids.max")}
    return out

def process(pid):
    out = {"pid": int(pid)}
    raw = read(f"/proc/{pid}/stat")
    if isinstance(raw, str):
        fields = raw.rsplit(") ", 1)[1].split()
        out.update(comm=raw.split("(", 1)[1].rsplit(")", 1)[0], state=fields[0], ppid=int(fields[1]), start_time_ticks=fields[19], rss_pages=int(fields[21]))
    status = read(f"/proc/{pid}/status")
    out["status_selected"] = [line for line in status.splitlines() if line.startswith(("Name:", "Uid:", "NSpid:", "Cpus_allowed_list:", "Mems_allowed_list:"))] if isinstance(status, str) else status
    out["cgroup"] = cgroups(pid)
    return out

CONTAINER_CODE = '''import json,os,sys,shutil
from pathlib import Path
allow=json.loads(sys.argv[1]); env={k:v for k,v in os.environ.items() if k in allow}
paths=["/opt/dtk", "/opt/hyhal", "/opt/conda", "/usr/local/built-in-508-topo-input-tj-default.xml"]
out={"hostname":os.uname().nodename,"python_executable":sys.executable,"python_version":sys.version,"env_whitelist":env,"uid":os.getuid(),"paths":{p:{"exists":Path(p).exists(),"resolved":str(Path(p).resolve())} for p in paths},"python3_command":shutil.which("python3"),"python_command":shutil.which("python"),"devices":{"kfd":os.access("/dev/kfd",os.R_OK|os.W_OK),"render_nodes":[p.name for p in Path("/dev/dri").glob("renderD*")]}}
print(json.dumps(out))
'''

errors = []
expected = sys.argv[1]
HOSTNAMES = {"node27": "m03r2n07", "node28": "m03r2n08", "node30": "m03r2n10"}
if expected not in HOSTNAMES or os.uname().nodename != HOSTNAMES[expected]:
    raise SystemExit("Host identity outside authorized three-node set")
signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("inventory deadline")))
signal.alarm(50)
out = {"started_utc": now(), "hostname": os.uname().nodename, "boot_id": read("/proc/sys/kernel/random/boot_id"), "uptime": read("/proc/uptime"), "uid": os.getuid(), "cpu_count": os.cpu_count(), "cpu_online": read("/sys/devices/system/cpu/online"), "memory": read("/proc/meminfo"), "loadavg": read("/proc/loadavg"), "pressure": {name: read(f"/proc/pressure/{name}") for name in ("cpu", "memory", "io")}, "driver": {"galcore": read("/sys/module/galcore/version"), "amdgpu": read("/sys/module/amdgpu/version"), "kernel": os.uname().release}, "env_whitelist": filtered([k+"="+v for k,v in os.environ.items()]), "python": {"executable": sys.executable, "version": sys.version}, "numa": {}, "drm": [], "kfd": [], "hca": [], "kfd_processes": [], "errors": errors, "gpu_api_calls": 0}
for p in listing("/sys/devices/system/node"):
    if re.fullmatch(r"node[0-9]+", p.name):
        out["numa"][p.name] = {key: read(p / key) for key in ("cpulist", "distance", "meminfo")}
for p in listing("/sys/class/drm"):
    if re.fullmatch(r"card[0-9]+", p.name):
        dev = p / "device"
        row = {"card": p.name, "pci_chain": chain(dev), "render_nodes": [x.name for x in listing(dev / "drm") if x.name.startswith("renderD")], "sample_monotonic_ns": time.monotonic_ns(), "mem_info_vram_total": read(dev / "mem_info_vram_total"), "mem_info_vram_used": read(dev / "mem_info_vram_used"), "driver_resolved": str((dev / "driver").resolve())}
        if isinstance(row["mem_info_vram_total"], str) and isinstance(row["mem_info_vram_used"], str):
            row["total_minus_used_bytes"] = int(row["mem_info_vram_total"])-int(row["mem_info_vram_used"])
        out["drm"].append(row)
for p in listing("/sys/class/kfd/kfd/topology/nodes"):
    raw = read(p / "properties")
    props = dict(line.split(maxsplit=1) for line in raw.splitlines() if len(line.split(maxsplit=1)) == 2) if isinstance(raw, str) else {}
    if props.get("simd_count", "0") != "0":
        out["kfd"].append({"node": p.name, "gpu_id": read(p / "gpu_id"), "properties": props, "pci_chain": chain(f"/sys/class/drm/renderD{props.get('drm_render_minor')}/device")})
for p in listing("/sys/class/infiniband"):
    out["hca"].append({"name": p.name, "pci_chain": chain(p / "device"), "ports": {q.name: {key: read(q / key) for key in ("rate", "state", "phys_state", "link_layer")} for q in listing(p / "ports")}})
for p in listing("/sys/class/kfd/kfd/proc"):
    if p.name.isdigit():
        row = process(p.name)
        row["kfd_files"] = {q.name: read(q) for q in listing(p) if re.fullmatch(r"(vram|pasid).*", q.name)}
        out["kfd_processes"].append(row)
out["kfd_proc_visibility"] = {"path": "/sys/class/kfd/kfd/proc", "exists": Path("/sys/class/kfd/kfd/proc").exists(), "listed_count": len(out["kfd_processes"]), "zero_does_not_prove_no_owners": True}
out["process_summary"] = []
for p in listing("/proc"):
    if not p.name.isdigit():
        continue
    comm = read(p / "comm")
    if isinstance(comm, str) and ("python" in comm.lower() or "vllm" in comm.lower() or "sglang" in comm.lower()):
        row = process(p.name)
        try:
            argv = (p / "cmdline").read_bytes().decode(errors="replace").split("\0")
            row["is_fluid_worker"] = any("fluidckpt.cluster.worker" in a for a in argv)
            row["is_vllm"] = any("vllm" in a.lower() for a in argv)
            row["is_sglang"] = any("sglang" in a.lower() for a in argv)
            row["worker_args"] = {a: argv[i+1] for i,a in enumerate(argv[:-1]) if a in ("--gpu", "--gpu-id", "--port", "--node", "--bind", "--tp-rank")}
        except OSError as exc:
            row["argv_visibility_error"] = repr(exc)
        out["process_summary"].append(row)
out["listening_ports"] = command(["ss", "-H", "-lnt"])
out["docker_commands"] = []
ps = command(["docker", "ps", "-a", "--format", "{{.ID}} {{.Names}} {{.Image}} {{.Status}}"])
out["docker_commands"].append(ps)
ids = [line.split()[0] for line in ps["stdout"].splitlines()][:40] if ps["returncode"] == 0 else []
out["containers"] = []
if ids:
    ins = command(["docker", "inspect", *ids], timeout=5)
    safe_command = {k:v for k,v in ins.items() if k != "stdout"}
    out["docker_commands"].append(safe_command)
    for ci in json.loads(ins["stdout"]) if ins["returncode"] == 0 else []:
        state = ci["State"]
        row = {"name": ci["Name"].lstrip("/"), "id": ci["Id"], "image_id": ci["Image"], "image_ref": ci["Config"].get("Image"), "created": ci["Created"], "started": state["StartedAt"], "pid": state["Pid"], "running": state["Running"], "env_whitelist": filtered(ci["Config"].get("Env")), "mounts": [{k:m.get(k) for k in ("Type", "Source", "Destination", "RW")} for m in ci.get("Mounts", [])], "host_config": {k:ci["HostConfig"].get(k) for k in ("Privileged", "NetworkMode", "IpcMode", "Memory", "NanoCpus", "CpusetCpus", "CpusetMems", "Devices", "GroupAdd")}}
        if row["running"]:
            row["cgroup"] = cgroups(state["Pid"])
        out["containers"].append(row)
images = command(["docker", "images", "--no-trunc", "--format", "{{.ID}} {{.Repository}}:{{.Tag}}"], timeout=5)
out["docker_commands"].append(images)
image_ids = sorted(set(line.split()[0] for line in images["stdout"].splitlines()))[:40] if images["returncode"] == 0 else []
out["images"] = []
if image_ids:
    ins = command(["docker", "image", "inspect", *image_ids], timeout=5)
    out["docker_commands"].append({k:v for k,v in ins.items() if k != "stdout"})
    for ii in json.loads(ins["stdout"]) if ins["returncode"] == 0 else []:
        out["images"].append({"id": ii["Id"], "repo_tags": ii.get("RepoTags"), "repo_digests": ii.get("RepoDigests"), "created": ii["Created"], "env_whitelist": filtered(ii["Config"].get("Env"))})
candidates = [c for c in out["containers"] if c["running"] and (c["name"].startswith("zjn") or "fluid" in c["name"])]
candidates.sort(key=lambda c: ("fluid-basic" not in c["name"], "fluid" not in c["name"], c["name"]))
for ci in candidates[:3]:
    cmd = command(["docker", "exec", "-i", ci["id"], "python3", "-", json.dumps(sorted(ALLOW))], timeout=6, stdin=CONTAINER_CODE)
    ci["python_metadata_command"] = cmd
    if cmd["returncode"] == 0:
        ci["python_metadata"] = json.loads(cmd["stdout"])
out.update(ended_utc=now(), collector_source_sha256=sys.argv[2], signals=0, node29_accesses=0)
print(json.dumps(out, indent=2))
