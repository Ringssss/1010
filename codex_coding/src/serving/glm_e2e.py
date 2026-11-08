"""End-to-end driver: keep GLM-5.3 alive across node27/28/29 and bring SGLang up from it.

  capture  --tag T --snap S          live SGLang (FLUIDCKPT_MODE=capture) -> keeper DRAM on its nodes
  spread   --snap S --to node28 ...  copy shards into another node's keeper DRAM (max-flow, IB)
  save     --snap S --node N --dir D keeper DRAM -> that node's SSD
  drop     --snap S --node N [--tps]  forget DRAM copies on a node (simulates eviction)
  restore  --snap S --tag T ...      launch SGLang --load-format dummy with the restore hook, plan
                                     a max-flow fill from every live copy, run it, time to ready
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(ROOT, "main_lib"))
from fluidckpt.serving import ctl  # noqa: E402
from fluidckpt.transport.flowplan import Copy, Demand, solve, to_ops  # noqa: E402

M = ctl.parse_mesh()
wid = lambda n, g: M.index((n, g))  # noqa: E731

# The rest of the snapshot travels as opaque bytes, but the MLA fused weights (w_kc/w_vc) are
# *derived* by post_load_weights and held as plain attributes, so a restoring skeleton cannot
# discover their shape. The capture side records them in a small manifest, which must be staged
# somewhere both restore nodes can read before SGLang starts. NFS is visible everywhere.
MANIFEST_DIR = "/data/node28/shared/zhujianian/fluidckpt_manifests"


def stage_manifest(tag, capture_node):
    """Copy the derived-attr manifest off the capture node's handoff dir to NFS. Returns its path."""
    os.makedirs(MANIFEST_DIR, exist_ok=True)
    dst = f"{MANIFEST_DIR}/{tag}.json"
    subprocess.check_call(["bash", "-c", f"rm -f {dst}"])
    for host in (capture_node,):
        src = f"/dev/shm/fluidckpt/{tag}/derived.json"
        c = ctl.ctr(host)
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", host,
                            f"docker exec {c} cat {src} > {dst}.tmp && mv {dst}.tmp {dst}"],
                           capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(dst):
            n = len(json.load(open(dst)))
            print(f"[manifest] staged {n} derived tensors from {host} -> {dst}", flush=True)
            return dst
    raise RuntimeError(f"no derived.json on {capture_node} for tag {tag}: "
                       f"was the capture run with FLUIDCKPT_MODE=capture?")


def inventory(snap, ssd_dirs=()):
    """Where does every tp of `snap` live right now? -> {tp: [Copy]}, {tp: (index, meta)}."""
    st = ctl.all_call(M, "status")
    inv, idx = {}, {}
    for (n, g), s in st.items():
        for key, gib in s["shards"].items():
            sn, tp = key.split("/")
            if sn != snap:
                continue
            tp = int(tp)
            inv.setdefault(tp, []).append(Copy("dram", n, gpu=g))
            if tp not in idx:
                r = ctl.get(n, g, f"index?snap={snap}&tp={tp}")
                idx[tp] = (r["index"], r["meta"])
    for node, d in ssd_dirs:
        for tp in range(16):
            j = f"{d}/rank{tp}.json"
            if not os.path.exists(j):
                continue
            inv.setdefault(tp, []).append(Copy("ssd", node, path=f"{d}/rank{tp}.bin"))
            if tp not in idx:
                jj = json.load(open(j))
                idx[tp] = (jj["index"], jj["meta"])
    return inv, idx


def nbytes_of(index):
    return max(o + b for o, b in index.values())


def run_flow(snap, demands, idx, into, tag=None, caps=None):
    T, split, load = solve(demands, caps)
    ops = to_ops(demands, split, wid)
    parts = {w for o in ops for w in (o.get("src_w"), o["dst_w"]) if w is not None}
    body = {"ops": ops, "into": into, "tag": tag, "snap": snap,
            "index": {str(d.tp): idx[d.tp][0] for d in demands},
            "meta": {str(d.tp): idx[d.tp][1] for d in demands}}
    for o in ops:
        o.setdefault("snap", snap)
    hot = sorted(load.items(), key=lambda x: -x[1])[:4]
    print(f"[plan] {len(ops)} ops, {sum(o['n'] for o in ops)/2**30:.1f} GiB, LP T={T:.2f}s, "
          f"busiest={[(k, round(v, 1)) for k, v in hot]}", flush=True)
    by = {}
    for o in ops:
        by[o["kind"]] = by.get(o["kind"], 0) + o["n"]
    print("[plan] bytes by kind:", {k: f"{v/2**30:.1f}GiB" for k, v in by.items()}, flush=True)
    t0 = time.time()
    res = ctl.all_call(M, "flow", body, only={M[w] for w in parts})
    wall = time.time() - t0
    return {"T_lp": T, "wall": wall, "ops": len(ops), "by_kind": by, "workers": {f"{k[0]}:{k[1]}": v for k, v in res.items()}}


def cmd_capture(a):
    nodes = a.nodes.split(",")
    t0 = time.time()
    r = ctl.all_call(M, "capture", {"tag": a.tag, "snap": a.snap}, only={m for m in M if m[0] in nodes})
    rows = [x for v in r.values() for x in v]
    tot = sum(x["GiB"] for x in rows)
    print(json.dumps({"capture_wall_s": time.time() - t0, "GiB": tot, "ranks": len(rows),
                      "d2h_GBps_median": sorted(x["d2h_GBps"] for x in rows)[len(rows) // 2],
                      "pin_s_max": max(x["pin_s"] for x in rows)}, indent=1))
    # The restore nodes' /dev/shm gets wiped before they start, so the derived-attr manifest has to
    # live somewhere durable and shared. Stage it now, while the capture nodes still hold it.
    stage_manifest(a.tag, nodes[0])
    print(json.dumps({"manifest": f"{MANIFEST_DIR}/{a.tag}.json"}, indent=1))


def cmd_spread(a):
    """Place tps on the target node's workers round-robin (the node may have fewer GPUs)."""
    inv, idx = inventory(a.snap)
    tps = [int(x) for x in a.tps.split(",")] if a.tps else sorted(inv)
    workers = [m for m in M if m[0] == a.to]
    demands = []
    for i, tp in enumerate(tps):
        n, g = workers[i % len(workers)]
        srcs = [c for c in inv[tp] if c.node != a.to]
        demands.append(Demand(tp, n, g, nbytes_of(idx[tp][0]), srcs))
    r = run_flow(a.snap, demands, idx, "dram")
    gib = sum(d.nbytes for d in demands) / 2**30
    print(json.dumps({"spread_to": a.to, "GiB": gib, "wall_s": r["wall"], "GBps": gib * 2**30 / r["wall"] / 1e9,
                      "T_lp": r["T_lp"]}, indent=1))


def cmd_save(a):
    r = ctl.all_call(M, "save", {"snap": a.snap, "dir": a.dir}, only={m for m in M if m[0] == a.node})
    rows = [x for v in r.values() for x in v]
    print(json.dumps({"saved": len(rows), "GiB": sum(x["GiB"] for x in rows), "max_s": max(x["s"] for x in rows)}))


def cmd_drop(a):
    ctl.all_call(M, "drop", {"snap": a.snap}, only={m for m in M if m[0] == a.node})
    print("dropped", a.snap, "on", a.node)


def ready_time(log, t0, timeout):
    t_end = time.time() + timeout
    while time.time() < t_end:
        try:
            s = open(log, errors="ignore").read()
        except OSError:
            s = ""
        if "fired up" in s:
            return time.time() - t0
        if "Traceback" in s and "Error" in s:
            raise RuntimeError(f"server failed, see {log}")
        time.sleep(0.2)
    raise TimeoutError(log)


def cmd_restore(a):
    inv, idx = inventory(a.snap, [tuple(x.split(":", 1)) for x in a.ssd] if a.ssd else ())
    nodes = [a.node0, a.node1]
    demands = []
    for tp in range(16):
        n, g = nodes[tp // 8], tp % 8
        srcs = [c for c in inv.get(tp, []) if not (c.kind == "dram" and c.node == n and c.gpu != g)
                and not (c.kind == "ssd" and c.node != n)]
        if a.only_kinds:
            srcs = [c for c in srcs if c.kind in a.only_kinds.split(",")]
        demands.append(Demand(tp, n, g, nbytes_of(idx[tp][0]), srcs))
    # Wipe stale handoff state on *both* restore nodes: a leftover rank<N>.done or derived.json from
    # an earlier run of this tag would otherwise be picked up ahead of the staged manifest and the
    # keeper's fresh one, making the restore silently replay the wrong snapshot.
    for n in set(nodes):
        subprocess.call(["bash", "-c", f"ssh {n} 'rm -rf /dev/shm/fluidckpt/{a.tag}'"])
    env = dict(os.environ, FLUIDCKPT_MODE="restore", PORT=str(a.port))
    man = f"{MANIFEST_DIR}/{a.tag}.json"
    if os.path.exists(man):
        env["FLUIDCKPT_MANIFEST"] = man
        print(f"[restore] manifest {man}", flush=True)
    else:
        # Non-fatal: a same-node restore can also find the capture's copy in the shared handoff dir.
        print(f"[restore] WARNING: no staged manifest at {man}; relying on the local handoff dir",
              flush=True)
    t0 = time.time()
    subprocess.check_call(["bash", f"{ROOT}/scripts/glm_serve.sh", a.tag, a.node0, a.node1, "--load-format", "dummy"],
                          env=env, cwd=ROOT)
    flow = {}
    th = threading.Thread(target=lambda: flow.update(run_flow(a.snap, demands, idx, "hbm", tag=a.tag)))
    th.start()
    t_ready = ready_time(f"{ROOT}/logs/{a.tag}_n0.log", t0, a.timeout)
    th.join()
    out = {"tag": a.tag, "t_ready_s": t_ready, "flow_wall_s": flow["wall"], "T_lp": flow["T_lp"],
           "by_kind_GiB": {k: v / 2**30 for k, v in flow["by_kind"].items()},
           "workers": flow["workers"]}
    json.dump(out, open(f"{ROOT}/benchmarks/results/glm_e2e_{a.tag}.json", "w"), indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "workers"}, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("capture"); p.add_argument("--tag", required=True); p.add_argument("--snap", required=True)
    p.add_argument("--nodes", default="node27,node29")
    p = sp.add_parser("spread"); p.add_argument("--snap", required=True); p.add_argument("--to", required=True)
    p.add_argument("--tps", default="")
    p = sp.add_parser("save"); p.add_argument("--snap", required=True); p.add_argument("--node", required=True)
    p.add_argument("--dir", required=True)
    p = sp.add_parser("drop"); p.add_argument("--snap", required=True); p.add_argument("--node", required=True)
    p = sp.add_parser("restore"); p.add_argument("--snap", required=True); p.add_argument("--tag", required=True)
    p.add_argument("--node0", default="node27"); p.add_argument("--node1", default="node29")
    p.add_argument("--ssd", action="append"); p.add_argument("--only-kinds", default="")
    p.add_argument("--port", type=int, default=31753); p.add_argument("--timeout", type=float, default=3600)
    a = ap.parse_args()
    {"capture": cmd_capture, "spread": cmd_spread, "save": cmd_save, "drop": cmd_drop, "restore": cmd_restore}[a.cmd](a)
