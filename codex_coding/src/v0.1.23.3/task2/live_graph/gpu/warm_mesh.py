"""Warm every cross-node pair group, then (optionally) run a flow.

Why this exists: a pair-group broadcaster that has never executed a collective deadlocks on its
first real transfer -- both ends create the communicator, the send is issued on a side stream, and
neither returns. A 1-byte broadcast on the default stream per pair (in the same order on every rank)
forces communicator creation up front, after which real transfers run at line rate.

Usage (on node27):
  python3 warm_mesh.py --mesh node27:0-3,... [--also-flow-selftest]
"""
import argparse
import json
import sys
import time

from mesh_ctl import all_call, parse_mesh


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mesh", required=True)
    ap.add_argument("--only", default="", help="comma list of pairs 'i-j' to warm (default: all cross-node)")
    a = ap.parse_args(argv)

    members = parse_mesh(a.mesh)
    rank = {m: i for i, m in enumerate(members)}
    node_of = {i: m[0] for i, m in enumerate(members)}
    pairs = [[i, j] for i in range(len(members)) for j in range(i + 1, len(members))
             if node_of[i] != node_of[j]]
    if a.only:
        keep = {tuple(int(x) for x in p.split("-")) for p in a.only.split(",")}
        pairs = [p for p in pairs if tuple(p) in keep]

    # every rank must create the groups in the same order, so pass the identical list everywhere
    t0 = time.time()
    out = all_call(members, "warm", {"pairs": pairs}, timeout=3600)
    wall = time.time() - t0
    print(json.dumps({"event": "warm_done", "pairs": len(pairs), "wall_s": round(wall, 2),
                      "max_rank_s": round(max(v["warm_s"] for v in out.values()), 2)}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
