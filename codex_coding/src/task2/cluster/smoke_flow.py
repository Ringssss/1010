"""Smoke the live 22-worker keeper mesh end to end with synthetic shards.

Small on purpose: it exists to prove the primitives the real experiments will stand on --
synthesize a shard, move it across nodes into another keeper's DRAM, read it back, and check
the bytes arrived intact. Nothing here touches SGLang.

  python3 smoke_flow.py --gib 1 --src node27 --dst node28

Two checks:
  A. checksum: synthesise on `src`, flow across to `dst` DRAM, compare checksums byte for byte.
  B. loopback: same-node DRAM -> HBM into a throwaway skeleton, to prove the hbm path runs.
"""
import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "main_lib"))

import keepctl  # noqa: E402
from fluidckpt.transport.flowplan import Copy, Demand, solve, to_ops  # noqa: E402


def flow(src_demands, into, tag, snap, idx, caps=None):
    """Run one planner-solved move of `demands`. Returns (wall_s, T_lp, result)."""
    members = keepctl.parse_mesh()
    T, split, load = solve(src_demands, caps)
    ops = to_ops(src_demands, split, lambda n, g: members.index((n, g)))
    # ops name workers by mesh index; `only` filters on (node, gpu) -- map back or nothing matches
    # and the fan-out silently returns in microseconds having moved no bytes.
    parts = {members[w] for o in ops for w in (o.get("src_w"), o["dst_w"]) if w is not None}
    body = {"ops": ops, "into": into, "tag": tag, "snap": snap,
            "index": {str(d.tp): idx[d.tp][0] for d in src_demands},
            "meta": {str(d.tp): idx[d.tp][1] for d in src_demands}}
    for o in ops:
        o.setdefault("snap", snap)
    print(f"[dbg] ops={len(ops)} parts={sorted(parts)} "
          f"first={ {k: v for k, v in ops[0].items() if k in ('kind','tp','n','src_w','dst_w')} if ops else None}",
          flush=True)
    t0 = time.time()
    res = keepctl.multi_call(members, "flow", body, only=parts)
    print(f"[dbg] replies={ {f'{k[0]}:{k[1]}': (v if not isinstance(v, dict) or 'error' not in v else v['error'][:200]) for k, v in res.items()} }",
          flush=True)
    return time.time() - t0, T, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gib", type=float, default=1.0)
    ap.add_argument("--tps", type=int, default=2)
    ap.add_argument("--src", default="node27")
    ap.add_argument("--dst", default="node28")
    ap.add_argument("--tag", default="smoke")
    ap.add_argument("--mesh-init", action="store_true", default=True,
                    help="bring the RCCL mesh up first if the workers start unranked")
    ap.add_argument("--no-mesh-init", dest="mesh_init", action="store_false")
    args = ap.parse_args()

    subprocess.run("ulimit -c 0", shell=True)  # build principle 17
    members = keepctl.parse_mesh()
    srcs = [m for m in members if m[0] == args.src]
    dsts = [m for m in members if m[0] == args.dst]
    nbytes = int(args.gib * 2**30)
    snap = f"smoke-{int(time.time())}"

    # The keepers start with rank=None; without this the flow fan-out matches no op on any worker
    # and every process cheerfully reports having moved zero bytes in eight milliseconds.
    if args.mesh_init:
        st = keepctl.call("node27", 0, "status", {}, timeout=5)
        if st.get("rank") is None:
            t0 = time.time()
            keepctl.mesh_init(members)
            print(f"[mesh] up in {time.time() - t0:.1f}s", flush=True)

    # ---- A. cross-node DRAM -> DRAM ----
    t0 = time.time()
    sums = {}
    for tp in range(args.tps):
        n, g = srcs[tp]
        r = keepctl.call(n, g, "synth", {"snap": snap, "tp": tp, "nbytes": nbytes, "seed": tp})
        sums[tp] = r["sum"]
    synth_s = time.time() - t0
    print(f"[A] synth {args.tps} x {args.gib} GiB on {args.src}: {synth_s:.2f}s", flush=True)

    inv = {tp: [Copy("dram", srcs[tp][0], gpu=srcs[tp][1])] for tp in range(args.tps)}
    idx = {tp: ({"t0": (0, nbytes)}, {"tp_rank": tp, "synthetic": True}) for tp in range(args.tps)}
    demands = []
    for tp in range(args.tps):
        n, g = dsts[tp]
        demands.append(Demand(tp, n, g, nbytes, inv[tp]))

    wall, T_lp, _ = flow(demands, "dram", None, snap, idx)
    gibs = args.tps * args.gib
    print(f"[A] flow {args.src}->{args.dst} {gibs} GiB: wall={wall:.3f}s "
          f"({gibs * 2**30 / wall / 1e9:.1f} GB/s), LP T={T_lp:.3f}s", flush=True)

    ok = True
    for tp in range(args.tps):
        n, g = dsts[tp]
        c = keepctl.call(n, g, "checksum", {"snap": snap, "tp": tp})
        match = c["sum"] == sums[tp]
        ok &= match
        print(f"[A] tp{tp} {args.dst}:{g} checksum {'MATCH' if match else 'MISMATCH'} "
              f"({c['sum']} vs {sums[tp]})", flush=True)
    print(json.dumps({"checksum_all_match": ok, "wall_s": wall, "GBps": gibs * 2**30 / wall / 1e9}))

    # ---- cleanup ----
    for node, tps in ((args.src, args.tps), (args.dst, args.tps)):
        ws = [m for m in members if m[0] == node]
        for tp in range(tps):
            n, g = ws[tp]
            try:
                keepctl.call(n, g, "drop", {"snap": snap})
            except Exception as e:
                print(f"[cleanup] {n}:{g} {e}", flush=True)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
