"""Ceiling of network -> staging -> HBM when every byte follows the topology:
each HCA port fills staging on its own NUMA node, never feeds a card on its own PCIe root, and
each card has its own lander. Source: affinity_probe.py src on a peer.
  land_probe4.py <src.json> <my ib0 ip> <out.log>"""
import json, queue, sys, threading, time
import torch
from fluidckpt.live import rdma
from fluidckpt.live.noded import prefer_numa
M = 1 << 20; G = 1 << 30; CH = 64 * M; NIC = [0, 1, 4, 5]
GN = {0: 0, 1: 3, 2: 2, 3: 1, 4: 4, 5: 7, 6: 6, 7: 5}
info = json.load(open(sys.argv[1])); me = sys.argv[2]; out = open(sys.argv[3], "a")
def say(**k):
    line = json.dumps(k); out.write(line + "\n"); out.flush(); print(line, flush=True)
SL = 40
S = {}
for p in range(4):
    prefer_numa(NIC[p], strict=True); t = torch.empty(SL * CH, dtype=torch.uint8, pin_memory=True); prefer_numa(None)
    S[p] = (t, [rdma.Region(q, t.data_ptr(), t.numel(), False) for q in range(4)])
conns = {p: [rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(info["ip"], p), info["port"]) for _ in range(2)] for p in range(4)}
bufs = {}
def run(tag, cards, per_card_gib=12, nconn=1, strict=True, ports=(0, 1, 2, 3)):
    for c in cards:
        if c not in bufs:
            bufs[c] = torch.empty(per_card_gib * G, dtype=torch.uint8, device=f"cuda:{c}"); bufs[c].zero_(); torch.cuda.synchronize(f"cuda:{c}")
    n = per_card_gib * G; best = 0
    for rep in range(3):
        free = {p: queue.Queue() for p in ports}; [free[p].put(s) for p in ports for s in range(SL)]
        todo = {c: list(range(0, n, CH)) for c in cards}; lock = threading.Lock()
        lq = {c: queue.Queue() for c in cards}; moved = {p: 0 for p in ports}
        def puller(p, ci):
            # cards this port may feed: not on its own PCIe root (when another choice exists)
            ok = [c for c in cards if GN[c] != NIC[p]] or list(cards)
            t, regs = S[p]; i = 0
            while True:
                with lock:
                    cs = [c for c in ok if todo[c]]
                    if not cs: return
                    c = max(cs, key=lambda c: len(todo[c])); off = todo[c].pop()
                s = free[p].get()
                k = (off // CH + p) % 8
                r = info["regions"][str(k)]
                conns[p][ci].read(r["addr"] + (off % (2 * G - CH)) // CH * CH, r["rkeys"][p], t.data_ptr() + s * CH, regs[p].lkey, CH, 4 * M, 8)
                moved[p] += CH
                lq[c].put((p, s, off))
        def lander(c):
            dev = f"cuda:{c}"; b = bufs[c]
            while True:
                it = lq[c].get()
                if it is None: return
                batch = [it]
                while len(batch) < 16:
                    try: x = lq[c].get_nowait()
                    except queue.Empty: break
                    if x is None: lq[c].put(None); break
                    batch.append(x)
                for p, s, off in batch: b[off:off + CH].copy_(S[p][0][s * CH:(s + 1) * CH], non_blocking=True)
                torch.cuda.synchronize(dev)
                for p, s, off in batch: free[p].put(s)
        pt = [threading.Thread(target=puller, args=(p, ci)) for p in ports for ci in range(nconn)]
        lt = [threading.Thread(target=lander, args=(c,)) for c in cards]
        t0 = time.time(); [x.start() for x in pt + lt]; [x.join() for x in pt]; [lq[c].put(None) for c in cards]; [x.join() for x in lt]
        dt = time.time() - t0
        if len(cards) * n / dt / 1e9 > best: best = len(cards) * n / dt / 1e9; mv = dict(moved)
    say(case=tag, cards=list(cards), ports=list(ports), conns_per_port=nconn, total_GBps=round(best, 1),
        per_card_GBps=round(best / len(cards), 1), port_share_GiB={p: round(v / G, 1) for p, v in mv.items()})
run("1 card (no shared root)", [1])
run("2 cards (no shared root)", [1, 2])
run("2 cards, 2 conns/port", [1, 2], nconn=2)
run("4 cards (no shared root)", [1, 2, 5, 6])
run("4 cards, 2 conns/port", [1, 2, 5, 6], nconn=2)
run("4 cards that each share a root with one port (port skips its own card)", [0, 3, 4, 7])
run("8 cards", [0, 1, 2, 3, 4, 5, 6, 7], per_card_gib=8)
run("8 cards, 2 conns/port", [0, 1, 2, 3, 4, 5, 6, 7], per_card_gib=8, nconn=2)
