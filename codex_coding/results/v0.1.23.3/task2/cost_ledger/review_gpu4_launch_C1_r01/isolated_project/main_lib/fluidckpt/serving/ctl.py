"""Client for the keeper mesh: start/stop workers, broadcast HTTP calls, run planned flows."""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

IP = {"node27": "12.11.3.27", "node28": "12.11.3.28", "node29": "12.11.3.29"}
PORT_BASE = 39500
MESH = "node27:0-7,node28:2-7,node29:0-7"


def parse_mesh(mesh=MESH):
    out = []
    for part in mesh.split(","):
        n, r = part.split(":")
        lo, hi = map(int, r.split("-"))
        out += [(n, g) for g in range(lo, hi + 1)]
    return out


def ctr(host):
    return subprocess.check_output(
        ["ssh", "-o", "BatchMode=yes", host,
         'docker ps --format "{{.Names}}" | grep -m1 -E "zjn-fluidckpt|zhujianian-fluidckpt"'], text=True).strip()


def start(mesh=MESH, master="12.11.3.27:39600"):
    members = parse_mesh(mesh)
    for host in sorted({n for n, _ in members}):
        c = ctr(host)
        cmds = []
        for n, g in members:
            if n != host:
                continue
            cmds.append(
                f"docker exec -d {c} bash -lc 'cd /home/zhujianian/zhujianian/fluidckpt_zjn && source /opt/dtk/env.sh >/dev/null 2>&1; "
                f"export NCCL_NET_PLUGIN=librccl-net-shca.so NCCL_SOCKET_IFNAME=ib0 GLOO_SOCKET_IFNAME=ib0 NCCL_DEBUG=WARN; "
                f"exec python3 -m fluidckpt.serving.keeper --node {host} --gpu {g} --mesh {mesh} --master {master} "
                f"> logs/keeper_{host}_{g}.log 2>&1'")
        subprocess.check_call(["ssh", "-o", "BatchMode=yes", host, " ; ".join(cmds)])
    # wait for HTTP
    for n, g in members:
        for _ in range(600):
            try:
                call(n, g, "status", timeout=2)
                break
            except Exception:
                time.sleep(0.5)
    return members


def stop(hosts=("node27", "node28", "node29")):
    for h in hosts:
        subprocess.call(["ssh", "-o", "BatchMode=yes", h,
                         f"docker exec {ctr(h)} pkill -f fluidckpt.serving.keeper"])


def call(node, gpu, op, body=None, timeout=3600):
    url = f"http://{IP[node]}:{PORT_BASE + gpu}/{op}"
    if body is None and op in ("status", "events"):
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=json.dumps(body or {}).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{node}:{gpu} {op}: {e.read().decode()[:2000]}")


def get(node, gpu, path, timeout=60):
    with urllib.request.urlopen(f"http://{IP[node]}:{PORT_BASE + gpu}/{path}", timeout=timeout) as r:
        return json.loads(r.read())


def all_call(members, op, body=None, only=None, timeout=3600):
    """Call op on every member concurrently (collective ops need that). Returns {(n,g): reply}."""
    targets = [m for m in members if only is None or m in only]
    with ThreadPoolExecutor(len(targets)) as ex:
        futs = {m: ex.submit(call, m[0], m[1], op, body, timeout) for m in targets}
        return {m: f.result() for m, f in futs.items()}
