"""Offline check of the planner with borrowed ingress: no hardware, no daemons.

Builds a Pool object without connecting (Pool.__new__), gives it the node's port x card capacity
table, and asks flow_plan for the predicted time of restores the pilot actually ran (chunk sets of
9g-8b on one card, qwen36-35b on two cards), without relay and with relay cards chosen the way
relay_pick would on an idle node. Prints one JSON line per case.
  relay_lp_check.py <manifest_dir> <out.jsonl>
"""
import collections
import json
import sys
import types

from fluidckpt.live.pool import GiB, Pool

man_dir, out_path = sys.argv[1], sys.argv[2]
out = open(out_path, "w")


def say(**k):
    line = json.dumps(k)
    out.write(line + "\n")
    out.flush()
    print(line, flush=True)


NEAR = {0: (0, 2), 1: (3, 1), 2: (4, 6), 3: (7, 5)}
SOCKET = {c: (0 if c < 4 else 1) for c in range(8)}
PORT_SOCKET = {0: 0, 1: 0, 2: 1, 3: 1}


def cap_table():
    cap = {}
    for p in range(4):
        cap[p] = {}
        for c in range(8):
            cap[p][c] = 47.0 if c in NEAR[p] else (24.0 if SOCKET[c] == PORT_SOCKET[p] else 12.0)
    return cap


P = Pool.__new__(Pool)
P.direct_cap = {"node28": cap_table()}
P.topology = types.SimpleNamespace(resources={}, vertices={})
P.ssd_disk = {"node28": "nvme0n1"}
P.ledger, P.reserved = {}, collections.Counter()


def chunks_of(model, cards, local_frac=0.25, lhbm_frac=0.0):
    """The model's chunks, round-robin over `cards`; every chunk has remote DRAM copies on three
    peers, a share also a local DRAM copy, a share a copy parked in this node's HBM."""
    m = json.load(open(f"{man_dir}/{model}.json"))
    out, off = [], collections.Counter()
    for i, c in enumerate(m["chunks"]):
        card = cards[i % len(cards)]
        cid, n = c[0], c[3]                      # manifest chunk: [id, file, offset, nbytes, ...]
        cands = [{"k": "dram", "node": s} for s in ("node27", "node29", "node30")]
        if (i % 100) < local_frac * 100:
            cands.append({"k": "ldram"})
        if (i % 100) >= 100 - lhbm_frac * 100:
            cands.append({"k": "lhbm"})
        out.append({"id": cid, "n": n, "card": card, "off": off[card], "cands": cands})
        off[card] += n
    return out


def relay_for(cards):
    """What relay_pick returns on an idle node: per port whose near cards are not targets, its
    best near card that is not a target."""
    cap = P.direct_cap["node28"]
    r = {}
    for p in cap:
        near = sorted(cap[p], key=lambda c: -cap[p][c])[:2]
        if any(c in cards for c in near):
            continue
        r[p] = near[0]
    return r


cases = [("9g-8b", [1]), ("qwen36-35b", [1, 2]), ("clover-35b", [0, 1]), ("qwen36-35b", [1, 3])]
for model, cards in cases:
    for lhbm in (0.0, 0.3):
        for use_relay in (False, True):
            ch = chunks_of(model, cards, lhbm_frac=lhbm)
            relay = relay_for(cards) if use_relay else {}
            pred, info = P.flow_plan(ch, "node28", cards, [0, 1, 2, 3], None, mode="lp", use_matrix=True,
                                     live=False, relay=relay)
            by = collections.Counter(c["plan_cls"] for c in ch)
            gib = sum(c["n"] for c in ch) / GiB
            say(model=model, cards=cards, lhbm_frac=lhbm, relay=relay, GiB=round(gib, 2), predicted_s=round(pred, 4),
                GBps=round(gib * GiB / pred / 1e9, 1), plan_GiB={k: round(sum(c["n"] for c in ch if c["plan_cls"] == k) / GiB, 2) for k in by},
                flow_GB=info.get("flow_GB"))
