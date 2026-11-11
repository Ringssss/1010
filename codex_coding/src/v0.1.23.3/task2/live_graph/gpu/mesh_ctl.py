"""Client for the live keeper mesh on containers zjn-fluid-live-r01-<n>.

Start/stop workers, initialise the RCCL world, and drive synthetic + real materialisation flows.
Owns the mapping "node:gpu" -> container-visible ordinal: inside each live container only our free
cards are exposed (HIP_VISIBLE_DEVICES), so host GPU 4 is ordinal 0 on node27/node29.

Commands:
  start     launch one keeper per (node, ordinal) in its node's container
  stop      kill every keeper process
  mesh      init the distributed world and report ranks
  synth     create N-byte synthetic shards on given workers
  flow      move a shard's bytes to destination workers over chosen kinds
"""
import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

IP = {"node27": "12.11.3.27", "node28": "12.11.3.28", "node29": "12.11.3.29", "node30": "12.11.3.30"}
CONTAINER = {n: f"zjn-fluid-live-r01-{n[4:]}" for n in IP}
REPO = "/home/zhujianian/zhujianian/fluidckpt_zjn"
PORT_BASE = 39500
MASTER = "12.11.3.27:39600"


def parse_mesh(spec):
    out = []
    for part in spec.split(","):
        n, r = part.split(":")
        if "-" in r:
            lo, hi = map(int, r.split("-"))
        else:
            lo = hi = int(r)
        out += [(n, g) for g in range(lo, hi + 1)]
    return out


def sh(node, cmd, timeout=600):
    return subprocess.run(["ssh", "-n", "-o", "BatchMode=yes", node, cmd],
                          capture_output=True, text=True, timeout=timeout)


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
        raise RuntimeError(f"{node}:{gpu} {op}: {e.read().decode()[:3000]}")


def all_call(members, op, body=None, timeout=3600):
    with ThreadPoolExecutor(len(members)) as ex:
        futs = {m: ex.submit(call, m[0], m[1], op, body, timeout) for m in members}
        return {m: f.result() for m, f in futs.items()}


def start(members, spec, tp_size=2, extra=""):
    by_node = {}
    for n, g in members:
        by_node.setdefault(n, []).append(g)
    for node, gpus in by_node.items():
        cmds = []
        for g in gpus:
            cmds.append(
                f"docker exec -d {CONTAINER[node]} bash -lc 'cd {REPO}; "
                f"export PYTHONPATH=main_lib "
                f"LD_LIBRARY_PATH=/opt/rccl-rdma-sharp-plugins/lib:/opt/dtk/lib:/opt/dtk/lib64:/opt/hyhal/lib:/opt/hyhal/lib64:$LD_LIBRARY_PATH "
                f"NCCL_NET_PLUGIN=librccl-net-shca.so NCCL_SOCKET_IFNAME=ib0 GLOO_SOCKET_IFNAME=ib0; "
                f"ulimit -c 0; exec python3 -m fluidckpt.serving.keeper --node {node} --gpu {g} "
                f"--mesh {spec} --master {MASTER} --tp-size {tp_size} {extra} "
                f">> /root/keeper_{node}_{g}.log 2>&1'")
        r = sh(node, " ; ".join(cmds), timeout=120)
        if r.returncode:
            raise SystemExit(f"{node}: launch failed: {r.stderr[:500]}")
    for n, g in members:
        for _ in range(240):
            try:
                call(n, g, "status", timeout=3)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise SystemExit(f"{n}:{g} never answered")


def stop(members):
    for node in sorted({n for n, _ in members}):
        sh(node, f"docker exec {CONTAINER[node]} pkill -f 'fluidckpt[.]serving[.]keeper' || true")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["start", "stop", "mesh", "status", "synth", "flow", "pcie"])
    ap.add_argument("--mesh", default="node27:0-3,node28:0-3,node29:0-3,node30:0-3")
    ap.add_argument("--tp-size", type=int, default=2)
    ap.add_argument("--extra", default="")
    ap.add_argument("--snap", default="s1")
    ap.add_argument("--tp", type=int, default=0)
    ap.add_argument("--gib", type=float, default=2.0)
    ap.add_argument("--src", help="node:gpu of the shard holder")
    ap.add_argument("--dst", help="comma list of node:gpu destinations")
    ap.add_argument("--kinds", default="h2d,ib")
    ap.add_argument("--split", default="even", choices=["even", "h2d-only", "ib-only"],
                    help="how to divide the shard's bytes between local and IB paths")
    a = ap.parse_args(argv)
    members = parse_mesh(a.mesh)

    if a.cmd == "start":
        start(members, a.mesh, a.tp_size, a.extra)
        print(json.dumps({"started": len(members)}))
    elif a.cmd == "stop":
        stop(members)
        print(json.dumps({"stopped": len(members)}))
    elif a.cmd == "mesh":
        r = all_call(members, "mesh")
        print(json.dumps({"world": len(r), "ranks": {f"{k[0]}:{k[1]}": v["rank"] for k, v in r.items()}}))
    elif a.cmd == "status":
        r = all_call(members, "status")
        print(json.dumps({f"{k[0]}:{k[1]}": {"rank": v["rank"], "dist": v["dist"],
                                              "shards": v["shards"], "hbm": round(v["hbm_alloc_GiB"], 2)}
                          for k, v in r.items()}, indent=1))
    elif a.cmd == "synth":
        targets = [(n, int(g)) for n, g in (x.split(":") for x in a.dst.split(","))]
        body = {"snap": a.snap, "tp": a.tp, "nbytes": int(a.gib * 2**30), "seed": 7}
        r = {m: call(m[0], m[1], "synth", body) for m in targets}
        print(json.dumps({f"{k[0]}:{k[1]}": v for k, v in r.items()}))
    elif a.cmd == "flow":
        src = tuple(a.src.split(":"))
        src = (src[0], int(src[1]))
        dsts = [tuple([x.split(":")[0], int(x.split(":")[1])]) for x in a.dst.split(",")]
        n = int(a.gib * 2**30)
        kinds = a.kinds.split(",")
        idx = {"t0": [0, n // 2], "t1": [n // 2, n - n // 2]}
        st = call(src[0], src[1], "status")
        if f"{a.snap}/{a.tp}" not in st["shards"]:
            raise SystemExit(f"source {src} has no shard {a.snap}/{a.tp}; run synth first")
        ops = []
        per = n // len(dsts)
        for i, d in enumerate(dsts):
            off = i * per
            cnt = per if i < len(dsts) - 1 else n - off
            kind = kinds[i % len(kinds)]
            op = {"kind": kind, "tp": a.tp, "off": off, "n": cnt, "dst_w": -1}
            if kind == "ib":
                op["src_w"] = -1
            ops.append((d, op))
        # dst_w/src_w must be rank ids in the mesh spec
        rank = {m: i for i, m in enumerate(members)}
        for d, op in ops:
            op["dst_w"] = rank[d]
            if op["kind"] == "ib":
                op["src_w"] = rank[src]
        flow = {"ops": [op for _, op in ops], "into": "dram", "tag": "t", "snap": a.snap,
                "index": {str(a.tp): idx}, "meta": {str(a.tp): {"tp_rank": a.tp}},
                "chunk_bytes": 64 << 20}
        t0 = time.time()
        r = all_call(members, "flow", flow)
        total = time.time() - t0
        print(json.dumps({"wall_s": round(total, 3), "total_GiB": round(n / 2**30, 2),
                          "agg_GBps": round(n / total / 1e9, 2),
                          "workers": {f"{k[0]}:{k[1]}": {kk: vv for kk, vv in v.items()
                                                          if kk in ("total_s", "ib_s", "ib_bytes",
                                                                    "h2d_bytes", "ssd_bytes")}
                                      for k, v in r.items() if isinstance(v, dict)}}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
