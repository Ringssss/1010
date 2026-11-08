"""Keeper mesh control for the v0.2.0 keepalive-flow experiments.

Self-contained: creates/starts isolated keeper workers over ssh+docker, exposes
the HTTP ops used by the experiment drivers, and never touches other users' containers.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

IMAGE = "ee3b9807d9e9"  # harbor sglang:0.5.12-ubuntu22.04-dtk2604-py3.10-sf_b075
ROOT = "/home/zhujianian/zhujianian/fluidckpt_zjn"
IP = {"node27": "12.11.3.27", "node28": "12.11.3.28", "node29": "12.11.3.29"}
PORT_BASE = 39700
MASTER = "12.11.3.27:39800"
# node28 GPU0/1 are held by another user's sglang-glm52; use 2-7 there.
MESH = "node27:0-7,node28:2-7,node29:0-7"
CONTAINER = {"node27": "zjn-keep-v02-n27", "node28": "zjn-keep-v02-n28", "node29": "zjn-keep-v02-n29"}


def _ssh(node, cmd, timeout=180):
    argv = ["bash", "-lc", cmd] if node == "node27" else ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", node, cmd]
    return subprocess.check_output(argv, text=True, stderr=subprocess.STDOUT, timeout=timeout)


def parse_mesh(mesh=MESH):
    out = []
    for part in mesh.split(","):
        n, r = part.split(":")
        lo, hi = map(int, r.split("-"))
        out += [(n, g) for g in range(lo, hi + 1)]
    return out


def gpu_numa(node, gpus=None):
    """GPU index -> NUMA node, read from sysfs. HIP device i is /sys/class/drm/card{i+1} (card0 is
    the board VGA). The 8 cards on these nodes sit on 8 *different* NUMA nodes, so a single
    --numa for the whole node is wrong; each worker must be told its own."""
    out = subprocess.check_output(["bash", "-lc", f"ssh -o BatchMode=yes {node} "
        "'for c in /sys/class/drm/card[0-9]*/device/numa_node; do printf \"%s \" $(cat $c); done'"],
        text=True).split()
    m = {}
    for i, v in enumerate(out):
        if i == 0:
            continue                      # card0 is the VGA device, not a DCU
        try:
            m[i - 1] = int(v)
        except ValueError:
            pass
    return {g: m[g] for g in (gpus if gpus is not None else m) if g in m}


def start(mesh=MESH, port_base=PORT_BASE, master=MASTER, memory_backend="numa_registered", extra_env=None, tp_size=2):
    members = parse_mesh(mesh)
    for node in sorted({n for n, _ in members}):
        gpus = [g for n, g in members if n == node]
        numa = gpu_numa(node, gpus)
        cmds = []
        for g in gpus:
            numa_arg = f" --numa {numa[g]}" if memory_backend == "numa_registered" and g in numa else ""
            cmds.append(
                f"docker exec -d {CONTAINER[node]} bash -lc 'cd {ROOT} && source /opt/dtk/env.sh >/dev/null 2>&1; "
                f"ulimit -c 0; mkdir -p logs; "
                f"export PYTHONPATH={ROOT}/main_lib NCCL_NET_PLUGIN=librccl-net-shca.so NCCL_SOCKET_IFNAME=ib0 "
                f"GLOO_SOCKET_IFNAME=ib0 NCCL_DEBUG=WARN HSA_FORCE_FINE_GRAIN_PCIE=1 HF_HUB_OFFLINE=1 {extra_env or ''}; "
                f"exec python3 -m fluidckpt.serving.keeper --node {node} --gpu {g} --port-base {port_base} "
                f"--mesh {mesh} --master {master} --memory-backend {memory_backend}{numa_arg} "
                f"--tp-size {tp_size} "
                f"> {ROOT}/logs/keeper_v02_{node}_{g}.log 2>&1'")
        _ssh(node, " ; ".join(cmds))
    # wait for HTTP readiness
    ready = {}
    for n, g in members:
        ok = False
        for _ in range(900):
            try:
                call(n, g, "status", {}, port_base, timeout=3)
                ok = True
                break
            except Exception:
                time.sleep(0.5)
        ready[(n, g)] = ok
    return members, ready


def stop(mesh=MESH):
    for node in sorted({n for n, _ in parse_mesh(mesh)}):
        _ssh(node, f"docker exec {CONTAINER[node]} pkill -f fluidckpt.serving.keeper || true", timeout=60)


GET_OPS = ("status", "events", "index")


def call(node, gpu, op, body=None, port_base=PORT_BASE, timeout=7200):
    url = f"http://{IP[node]}:{port_base + gpu}/{op}"
    # The keeper serves these from do_GET and rejects them on do_POST, so the verb follows the
    # op name -- deciding it from `body is None` broke every status poll that passed {}.
    get = op in GET_OPS or (op == "index" and body)
    if op == "index" and body:
        url += "?" + urllib.parse.urlencode(body)
    data = None if get else json.dumps(body or {}).encode()
    req = urllib.request.Request(url, data=data, method="GET" if get else "POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
        return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{node}:{gpu} {op}: {e.read().decode()[:2000]}") from e


def mesh_init(members, port_base=PORT_BASE):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(len(members)) as ex:
        futs = [ex.submit(call, n, g, "mesh", {}, port_base) for n, g in members]
        return [f.result() for f in futs]


def multi_call(members, op, body=None, only=None, bodies=None, port_base=PORT_BASE, timeout=7200):
    """Fan one op out over the mesh, returning {(node, gpu): reply}.

    `bodies` gives a per-worker body (indexed by mesh order); `only` restricts the fan-out to a set
    of mesh workers -- a collective flow only touches workers named as a src or dst.
    """
    from concurrent.futures import ThreadPoolExecutor
    targets = [m for m in members if only is None or m in only]
    with ThreadPoolExecutor(max(len(targets), 1)) as ex:
        futs = {}
        for i, (n, g) in enumerate(targets):
            b = bodies[i] if bodies is not None else body
            futs[(n, g)] = ex.submit(call, n, g, op, b, port_base, timeout)
        return {k: f.result() for k, f in futs.items()}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["start", "stop", "status", "mesh"])
    ap.add_argument("--mesh", default=MESH)
    a = ap.parse_args()
    if a.action == "stop":
        stop(a.mesh)
    elif a.action == "start":
        members, ready = start(a.mesh)
        print(json.dumps({"members": len(members), "ready": sum(ready.values()), "detail": {f"{n}:{g}": v for (n, g), v in ready.items()}}, indent=1))
    elif a.action == "mesh":
        members = parse_mesh(a.mesh)
        print(json.dumps([{"node": n, "gpu": g} for n, g in members], indent=0))
    else:
        members = parse_mesh(a.mesh)
        rows = []
        for n, g in members:
            try:
                s = call(n, g, "status", {}, timeout=5)
                rows.append({"node": n, "gpu": g, "ok": True, "events": len(s.get("events", []))})
            except Exception as e:
                rows.append({"node": n, "gpu": g, "ok": False, "error": str(e)[:120]})
        print(json.dumps(rows, indent=1))


def remote_python(node, argv, image=IMAGE, timeout=86400, env=None, mounts=()):
    """Run a driver script inside `node`'s keeper container, streamed to this stdout.

    The host python has no numpy/scipy/torch, so driver scripts execute in the container. The repo
    is already bind-mounted at the same path, so relative paths and ROOT-derived paths both work.
    """
    cmd = ["docker", "exec", "-i", "-w", ROOT, "-e", "PYTHONPATH=" + os.path.join(ROOT, "main_lib")]
    for k, v in (env or {}).items():
        cmd += ["-e", f"{k}={v}"]
    cmd += [CONTAINER[node], "bash", "-lc", "ulimit -c 0; exec " + " ".join(shlex.quote(x) for x in argv)]
    return _ssh(node, " ".join(shlex.quote(x) for x in cmd), timeout=timeout)
