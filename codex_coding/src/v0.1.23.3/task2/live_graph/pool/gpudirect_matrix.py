"""Port x card capacity of direct RDMA into device memory, then what a flow assignment buys.
  gpudirect_matrix.py <src.json> <my ib0 ip> <out.log>
(1) every (port, card) alone; (2) all ports at once under three assignments: naive (every port
feeds every card), matched by a max-flow LP on the measured matrix, and the LP's choice plus a
concurrent local H2D into the same cards (does host H2D share the budget of direct RDMA?)."""
import json, sys, threading, time
import numpy as np
import torch
from scipy.optimize import linprog
from fluidckpt.live import rdma
from fluidckpt.live.noded import prefer_numa
M = 1 << 20; G = 1 << 30; NIC = [0, 1, 4, 5]
info = json.load(open(sys.argv[1])); me = sys.argv[2]; out = open(sys.argv[3], "a")
def say(**k):
    line = json.dumps(k); out.write(line + "\n"); out.flush(); print(line, flush=True)
C = list(range(8)); NS = 4
g = {c: torch.zeros(4 * G, dtype=torch.uint8, device=f"cuda:{c}") for c in C}
reg = {c: [rdma.Region(p, g[c].data_ptr(), 4 * G, False) for p in range(4)] for c in C}
conns = {p: [rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(info["ip"], p), info["port"]) for _ in range(8)] for p in range(4)}
host = {}
def run(plan, secs=2.0, h2d=()):
    """plan: [(port, card)] streams; returns per-stream GB/s (+ H2D GB/s per card in h2d)."""
    stop = threading.Event(); n = [0] * len(plan); hn = {c: 0 for c in h2d}; used = {}
    def one(i, p, c):
        ci = used.setdefault(p, []); k = len(ci); ci.append(i)
        rr = info["regions"][str((i * 3 + NIC[p]) % 8)]
        while not stop.is_set():
            conns[p][k].read(rr["addr"], rr["rkeys"][p], g[c].data_ptr(), reg[c][p].lkey, 2 * G, 4 * M, 8); n[i] += 2 * G
    def h(c):
        dev = f"cuda:{c}"
        if c not in host:
            prefer_numa(c); host[c] = torch.empty(2 * G, dtype=torch.uint8, pin_memory=True); prefer_numa(None)
        while not stop.is_set():
            for off in range(0, 2 * G, 64 * M): g[c][2 * G + off:2 * G + off + 64 * M].copy_(host[c][off:off + 64 * M], non_blocking=True)
            torch.cuda.synchronize(dev); hn[c] += 2 * G
    for c in h2d:
        if c not in host: h_init = threading.Thread(target=lambda: None); prefer_numa(None)
    th = []
    for i, (p, c) in enumerate(plan):
        used.setdefault(p, [])
    used.clear()
    th = [threading.Thread(target=one, args=(i, p, c)) for i, (p, c) in enumerate(plan)] + [threading.Thread(target=h, args=(c,)) for c in h2d]
    for c in h2d:
        if c not in host:
            prefer_numa(c); host[c] = torch.empty(2 * G, dtype=torch.uint8, pin_memory=True); prefer_numa(None)
    t0 = time.time(); [x.start() for x in th]; time.sleep(secs); stop.set(); [x.join() for x in th]; dt = time.time() - t0
    return [round(v / dt / 1e9, 1) for v in n], {c: round(v / dt / 1e9, 1) for c, v in hn.items()}
mat = np.zeros((4, 8))
for p in range(4):
    for c in C:
        mat[p, c] = run([(p, c)], 1.2)[0][0]
    say(test="matrix", port=p, port_numa=NIC[p], GBps_by_card={c: mat[p, c] for c in C})
def lp(cards, port_cap=48.5, card_cap=48.5):
    """max total flow: x[p,c] <= mat[p,c] * share, sum_c x[p,c]/mat[p,c] <= 1 (a port's time), port and card caps."""
    idx = [(p, c) for p in range(4) for c in cards]
    A, b = [], []
    for p in range(4):                                   # the port is one pipe: time shares add to 1
        A.append([1 / max(mat[p, c], 1e-3) if q == p else 0 for q, c in idx]); b.append(1.0)
    for c in cards:
        A.append([1.0 if d == c else 0 for q, d in idx]); b.append(card_cap)
    r = linprog([-1] * len(idx), A_ub=A, b_ub=b, bounds=(0, None), method="highs")
    return {k: v for k, v in zip(idx, r.x) if v > 0.5}, -r.fun
for cards in ([1, 2], [1, 2, 5, 6], [0, 3, 4, 7], C):
    naive = [(p, c) for p in range(4) for c in cards]
    rn, _ = run(naive)
    flow, bound = lp(cards)
    # one stream per positive (port, card) pair; a port with two targets time-shares itself
    plan = sorted(flow)
    rm, _ = run(plan)
    say(test="assign", cards=cards, naive_total=round(sum(rn), 1), lp_total=round(sum(rm), 1), lp_bound=round(bound, 1),
        lp_plan={f"p{p}->c{c}": round(v, 1) for (p, c), v in flow.items()}, lp_streams=dict(zip([f"p{p}->c{c}" for p, c in plan], rm)))
    rh, hh = run(plan, h2d=cards)
    _, alone = run([], h2d=cards)
    say(test="direct+h2d", cards=cards, direct_total=round(sum(rh), 1), h2d_total=round(sum(hh.values()), 1),
        both=round(sum(rh) + sum(hh.values()), 1), h2d_alone=round(sum(alone.values()), 1))
