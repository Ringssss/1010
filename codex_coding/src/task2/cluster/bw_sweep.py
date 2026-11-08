"""Characterise cross-node keeper bandwidth: cold setup cost vs warm steady-state rate.

The first smoke run moved 2 GiB at 2.1 GB/s, ~20x under the 46-48.5 GB/s single-link
capability. Two candidates: (a) RCCL lazily establishes the pair connection on the first
collective and that setup dominates a small transfer; (b) the rate is genuinely that low.
Passes over the same size separate them: pass 0 is cold, pass 1+ reuses the pair
communicators created at mesh_init, so a rising-then-plateau curve means setup.

  python3 bw_sweep.py --gibs 1,2,4 --tps 2 --src node27 --dst node28 --repeat 3

Runs inside a keeper container (needs numpy/torch); keepctl.remote_python does that.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "main_lib"))

import keepctl  # noqa: E402
from fluidckpt.transport.flowplan import Copy, Demand, solve, to_ops  # noqa: E402


def move(members, demands, snap, idx, into="dram", caps=None):
    """Plan and execute one move. Returns (wall_s, T_lp, ops, replies).

    No separate warm() step: the keeper's `pair_of` communicators are created at mesh_init and
    reused, so the first pass over a size is cold and later passes are warm. That isolates cold
    setup cost without the fan-out hazard of warming pairs the caller does not fan out to --
    an un-invited rank never receives the request and the collective waits out its 600 s timeout.
    """
    T, split, _ = solve(demands, caps)
    ops = to_ops(demands, split, lambda n, g: members.index((n, g)))
    for o in ops:
        o.setdefault("snap", snap)
    parts = {members[w] for o in ops for w in (o.get("src_w"), o["dst_w"]) if w is not None}
    body = {"ops": ops, "into": into, "tag": None, "snap": snap,
            "index": {str(d.tp): idx[d.tp][0] for d in demands},
            "meta": {str(d.tp): idx[d.tp][1] for d in demands}}
    t0 = time.time()
    res = keepctl.multi_call(members, "flow", body, only=parts)
    return time.time() - t0, T, ops, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gibs", default="1,2,4")
    ap.add_argument("--tps", type=int, default=2)
    ap.add_argument("--src", default="node27")
    ap.add_argument("--dst", default="node28")
    ap.add_argument("--repeat", type=int, default=3, help="pass 0 is cold; later passes reuse comms")
    ap.add_argument("--no-mesh-init", action="store_true",
                    help="assume the RCCL mesh is already up (skip the 125 s init)")
    args = ap.parse_args()

    subprocess_guard()
    members = keepctl.parse_mesh()
    srcs = [m for m in members if m[0] == args.src]
    dsts = [m for m in members if m[0] == args.dst]
    snap = f"bw-{int(time.time())}"
    rows = []

    # Keepers boot unranked; every flow op is dropped until the RCCL mesh exists. 125 s for 22 ranks.
    if not args.no_mesh_init:
        st = keepctl.call("node27", 0, "status", {}, timeout=10)
        if st.get("rank") is None:
            t0 = time.time()
            keepctl.mesh_init(members)
            print(json.dumps({"ev": "mesh_up", "s": round(time.time() - t0, 1)}), flush=True)

    for gib in [float(x) for x in args.gibs.split(",")]:
        n = int(gib * 2**30)
        # A fresh snap per size: synth refuses to overwrite an existing snapshot, and reusing one
        # name made the second size abort with "snapshot already exists".
        osnap = f"{snap}-{int(gib * 100)}"
        # One synthetic storage per rank, so the index is a single contiguous range.
        idx = {tp: ({"t0": (0, n)}, {"tp_rank": tp, "synthetic": True}) for tp in range(args.tps)}
        t0 = time.time()
        for tp in range(args.tps):
            keepctl.call(srcs[tp][0], srcs[tp][1], "synth",
                         {"snap": osnap, "tp": tp, "nbytes": n, "seed": tp})
        synth_s = time.time() - t0

        total = args.tps * gib
        for rep in range(args.repeat):
            inv = {tp: [Copy("dram", srcs[tp][0], gpu=srcs[tp][1])] for tp in range(args.tps)}
            demands = [Demand(tp, dsts[tp][0], dsts[tp][1], n, inv[tp]) for tp in range(args.tps)]
            wall, T_lp, ops, res = move(members, demands, osnap, idx)
            ib = sum(v.get("ib_bytes", 0) for v in res.values())
            prep = max((v.get("prep_s", 0) for v in res.values()), default=0)
            ibs = max((v.get("ib_s", 0) for v in res.values()), default=0)
            row = {"GiB_per_rank": gib, "total_GiB": total, "pass": rep, "cold": rep == 0,
                   "wall_s": round(wall, 4), "GBps": round(total * 2**30 / wall / 1e9, 2),
                   "prep_s": round(prep, 3), "ib_s": round(ibs, 3),
                   "lp_T_s": round(T_lp, 4), "ib_GiB_moved": round(ib / 2**30, 3),
                   "synth_s": round(synth_s, 1)}
            rows.append(row)
            print(json.dumps(row), flush=True)

    print(json.dumps({"rows": rows}))
    for node, ws in ((args.src, srcs), (args.dst, dsts)):
        for tp in range(args.tps):
            for gib in [float(x) for x in args.gibs.split(",")]:
                try:
                    keepctl.call(ws[tp][0], ws[tp][1], "drop", {"snap": f"{snap}-{int(gib * 100)}"})
                except Exception as e:
                    print(f"[cleanup] {node} tp{tp} {gib}GiB: {e}", flush=True)


def subprocess_guard():
    """Build principle 17: a GPU crash must not be able to write core dumps onto /home."""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except Exception:
        pass


if __name__ == "__main__":
    main()
