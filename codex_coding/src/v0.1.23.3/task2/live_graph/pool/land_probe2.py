"""Follow-up: is the 35 GB/s a pipeline artifact or contention between NIC DMA and GPU DMA?
  (a) H2D alone while an unrelated RDMA stream runs into different memory;
  (b) the same pipeline into two cards at once."""
import json, queue, sys, threading, time
import torch
from fluidckpt.live import rdma
from fluidckpt.live.noded import prefer_numa
M = 1 << 20; G = 1 << 30
info = json.load(open(sys.argv[1])); me = sys.argv[2]; cards = [int(x) for x in sys.argv[3].split(",")]
n = info["nbytes"]; CH = 64 * M
gp = {c: torch.empty(n, dtype=torch.uint8, device=f"cuda:{c}") for c in cards}
for c in cards: gp[c].zero_(); torch.cuda.synchronize(f"cuda:{c}")
def pool(numa, nbytes):
    prefer_numa(numa); t = torch.empty(nbytes, dtype=torch.uint8, pin_memory=True); prefer_numa(None)
    return t, [rdma.Region(p, t.data_ptr(), t.numel(), False) for p in range(4)]
conns = [rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(info["ip"], p), info["port"]) for p in range(4)]
def say(**k): print(json.dumps(k), flush=True)
# (a)
A, Areg = pool(4, 4 * G); B, _ = pool(0, 4 * G); B.fill_(1)
stop = threading.Event(); moved = [0]
def bg(p):
    while not stop.is_set():
        conns[p].read(info["addr"], info["rkeys"][str(p)], A.data_ptr() + p * G, Areg[p].lkey, G, 4 * M, 8); moved[0] += G
def h2d(c, reps=6):
    dev = f"cuda:{c}"; t = time.time()
    for _ in range(reps):
        for off in range(0, 4 * G, CH): gp[c][off:off + CH].copy_(B[off:off + CH], non_blocking=True)
        torch.cuda.synchronize(dev)
    return reps * 4 * G / (time.time() - t) / 1e9
c0 = cards[0]
say(case="h2d alone", card=c0, GBps=round(h2d(c0), 2))
for nports in (1, 2, 4):
    stop.clear(); moved[0] = 0
    th = [threading.Thread(target=bg, args=(p,)) for p in range(nports)]; t0 = time.time(); [x.start() for x in th]
    time.sleep(0.2); r = h2d(c0); stop.set(); [x.join() for x in th]
    say(case=f"h2d while {nports}-port rdma runs into other memory", card=c0, h2d_GBps=round(r, 2),
        rdma_GBps=round(moved[0] / (time.time() - t0) / 1e9, 2))
del A, B
# (b) pipeline into several cards
S, Sreg = pool(None, 96 * CH)
def run(tag, use):
    free = queue.Queue(); [free.put(s) for s in range(96)]
    offs = [(c, off) for off in range(0, n, CH) for c in use]; lock = threading.Lock()
    lq = {c: queue.Queue() for c in use}
    def puller(p):
        while True:
            with lock:
                if not offs: return
                c, off = offs.pop()
            m = min(CH, n - off); s = free.get()
            conns[p].read(info["addr"] + off, info["rkeys"][str(p)], S.data_ptr() + s * CH, Sreg[p].lkey, m, 4 * M, 8)
            lq[c].put((s, off, m))
    def lander(c):
        dev = f"cuda:{c}"
        while True:
            it = lq[c].get()
            if it is None: return
            batch = [it]
            while len(batch) < 16:
                try: x = lq[c].get_nowait()
                except queue.Empty: break
                if x is None: lq[c].put(None); break
                batch.append(x)
            for s, off, m in batch: gp[c][off:off + m].copy_(S[s * CH:s * CH + m], non_blocking=True)
            torch.cuda.synchronize(dev)
            for s, off, m in batch: free.put(s)
    for rep in range(3):
        offs[:] = [(c, off) for off in range(0, n, CH) for c in use]
        pt = [threading.Thread(target=puller, args=(p,)) for p in range(4)]; lt = [threading.Thread(target=lander, args=(c,)) for c in use]
        t0 = time.time(); [x.start() for x in pt + lt]; [x.join() for x in pt]; [lq[c].put(None) for c in use]; [x.join() for x in lt]
        dt = time.time() - t0
        if rep: say(case=tag, cards=use, GBps=round(len(use) * n / dt / 1e9, 2), s=round(dt, 3))
run("pipeline 1 card", cards[:1])
if len(cards) > 1: run("pipeline 2 cards", cards[:2])
if len(cards) > 3: run("pipeline 4 cards", cards[:4])
