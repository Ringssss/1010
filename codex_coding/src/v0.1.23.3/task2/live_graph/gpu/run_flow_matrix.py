"""Real keeper flow: many sources into one GPU, measuring what the live graph plans on.

Phase 1 assumed a destination GPU can be fed by several entries at once (local H2D plus IB RDMA
from peer nodes), reaching ~201 GB/s aggregate against a 46 GB/s single-stream H2D. That estimate
was extrapolated from a profile mix, never executed. This runs it: synthetic shards on sources
across four nodes, each shard's whole byte range moved into one destination worker, every rank
concurrently, and reports per-worker and aggregate rates.

The keeper validates that one rank's byte range has exactly one destination, so multi-entry into a
single GPU is expressed as several TP indices (each a separate shard) all landing on the same
worker. That is also how a real TP shard set arrives: different tp ranks, one GPU.

Usage (on node27):
  python3 run_flow_matrix.py --dest node27:0 --sources node28:0,node29:0,node30:0,node27:1 \
      --local node27:0 --gib 2 --reps 1 --out <dir>
"""
import argparse
import json
import os
import sys
import time

from mesh_ctl import IP, all_call, call, parse_mesh, start, stop, PORT_BASE

DEFAULT_SPEC = "node27:0-3,node28:0-3,node29:0-3,node30:0-3"
SPEC = DEFAULT_SPEC
MEMBERS = parse_mesh(SPEC)
RANK = {m: i for i, m in enumerate(MEMBERS)}


def set_spec(spec):
    """The keeper mesh spec must match what the workers were started with, or rank ids differ."""
    global SPEC, MEMBERS, RANK
    SPEC = spec
    MEMBERS = parse_mesh(spec)
    RANK = {m: i for i, m in enumerate(MEMBERS)}
    return MEMBERS


def parse_ep(text):
    node, gpu = text.split(":")
    return (node, int(gpu))


def synth(ep, snap, tps, nbytes, run_id=""):
    """Create one shard per tp on this worker so each can be a separate source."""
    tag = f"{snap}-{ep[0]}-{ep[1]}" + (f"-{run_id}" if run_id else "")
    out = {}
    for tp in tps:
        body = {"snap": tag, "tp": tp, "nbytes": nbytes, "seed": 11 + tp}
        out[tp] = call(ep[0], ep[1], "synth", body)
    return tag, out


def run_flow(src, dst, tag, tp, nbytes, kind, chunk):
    """Move src's shard (tag, tp) entirely into dst over `kind` ("ib" or "h2d")."""
    idx = {"t0": [0, nbytes // 2], "t1": [nbytes // 2, nbytes - nbytes // 2]}
    op = {"kind": kind, "tp": tp, "off": 0, "n": nbytes, "dst_w": RANK[dst], "snap": tag}
    if kind in ("ib", "h2d"):
        op["src_w"] = RANK[src]
    flow = {"ops": [op], "into": "dram", "tag": "unused", "snap": tag,
            "index": {str(tp): idx}, "meta": {str(tp): {"tp_rank": tp}},
            "chunk_bytes": chunk, "executor": "staged"}
    t0 = time.time()
    replies = all_call([src, dst], "flow", flow, timeout=1800)
    return time.time() - t0, replies


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default="node27:0")
    ap.add_argument("--sources", default="node28:0,node29:0,node30:0,node27:1")
    ap.add_argument("--gib", type=float, default=2.0)
    ap.add_argument("--chunk-gib", type=float, default=0.0625)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--out", required=True)
    ap.add_argument("--spec", default=DEFAULT_SPEC, help="must equal the mesh keepers were started with")
    ap.add_argument("--run-id", default="", help="suffix so a rerun does not reuse an existing shard")
    ap.add_argument("--only", default="", help="comma list of source subset for a control run")
    a = ap.parse_args(argv)
    set_spec(a.spec)

    os.makedirs(a.out, exist_ok=True)
    dest = parse_ep(a.dest)
    sources = [parse_ep(x) for x in a.sources.split(",")]
    if a.only:
        keep = {parse_ep(x) for x in a.only.split(",")}
        sources = [s for s in sources if s in keep]
    nbytes = int(a.gib * 2**30)
    chunk = int(a.chunk_gib * 2**30)

    # one shard per source, tp index = position, all landing on the same destination worker
    plan = []
    for tp, src in enumerate(sources):
        plan.append({"tp": tp, "src": src})

    t = time.time()
    for p in plan:
        p["tag"], _ = synth(p["src"], "flowm", [p["tp"]], nbytes, run_id=a.run_id)
    print(json.dumps({"event": "synth_done", "s": round(time.time() - t, 2),
                      "per_shard_GiB": a.gib, "shards": len(plan)}), flush=True)

    rows = []
    for rep in range(a.reps):
        t0 = time.time()
        wall, replies = {}, {}
        for p in plan:
            kind = "h2d" if p["src"][0] == dest[0] else "ib"
            w, r = run_flow(p["src"], dest, p["tag"], p["tp"], nbytes, kind, chunk)
            wall[f"{p['src'][0]}:{p['src'][1]}"] = {"s": round(w, 4), "kind": kind,
                                                   "GBps": round(nbytes / w / 1e9, 2)}
            replies[f"{p['src'][0]}:{p['src'][1]}"] = {
                k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in (r.get(dest) or {}).items()
                if k in ("total_s", "ib_s", "prep_s", "ib_bytes", "h2d_bytes")}
        total = time.time() - t0
        row = {"rep": rep, "sources": len(plan), "GiB_each": a.gib,
               "sum_wall_s": round(total, 4),
               "aggregate_GBps": round(len(plan) * nbytes / total / 1e9, 2),
               "per_source": wall, "dest_replies": replies}
        rows.append(row)
        print(json.dumps(row), flush=True)

    with open(os.path.join(a.out, "flow_matrix.json"), "w") as fh:
        json.dump({"spec": SPEC, "dest": a.dest, "sources": a.sources, "GiB": a.gib, "rows": rows},
                  fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
