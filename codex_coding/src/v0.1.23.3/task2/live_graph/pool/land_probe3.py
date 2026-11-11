"""Which NIC port hurts which card? H2D rate on a card while ONE port receives RDMA into memory
on that port's own NUMA node; then the full pipeline restricted to port subsets."""
import json, queue, sys, threading, time
import torch
from fluidckpt.live import rdma
from fluidckpt.live.noded import prefer_numa
M = 1 << 20; G = 1 << 30; CH = 64 * M
info = json.load(open(sys.argv[1])); me = sys.argv[2]; cards = [int(x) for x in sys.argv[3].split(",")]
GNUMA = {0: 0, 1: 3, 2: 2, 3: 1, 4: 4, 5: 7, 6: 6, 7: 5}; NICN = [0, 1, 4, 5]; n = info["nbytes"]
def say(**k): print(json.dumps(k), flush=True)
def pin(numa, nbytes):
    prefer_numa(numa); t = torch.empty(nbytes, dtype=torch.uint8, pin_memory=True); prefer_numa(None); return t
conns = [rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(info["ip"], p), info["port"]) for p in range(4)]
A = [pin(NICN[p], G) for p in range(4)]; Areg = [rdma.Region(p, A[p].data_ptr(), G, False) for p in range(4)]
for c in cards:
    dev = f"cuda:{c}"; g = torch.empty(4 * G, dtype=torch.uint8, device=dev); g.zero_(); torch.cuda.synchronize(dev)
    B = pin(GNUMA[c], 4 * G); B.fill_(1)
    def h2d(reps=5):
        t = time.time()
        for _ in range(reps):
            for off in range(0, 4 * G, CH): g[off:off + CH].copy_(B[off:off + CH], non_blocking=True)
            torch.cuda.synchronize(dev)
        return round(reps * 4 * G / (time.time() - t) / 1e9, 1)
    row = {"card": c, "card_numa": GNUMA[c], "alone": h2d()}
    for p in (range(4) if len(sys.argv) < 5 else []):
        stop = threading.Event(); moved = [0]
        def bg():
            while not stop.is_set():
                conns[p].read(info["addr"], info["rkeys"][str(p)], A[p].data_ptr(), Areg[p].lkey, G, 4 * M, 8); moved[0] += G
        th = threading.Thread(target=bg); t0 = time.time(); th.start(); time.sleep(0.1); r = h2d(); stop.set(); th.join()
        row[f"with_port{p}(numa{NICN[p]})"] = [r, round(moved[0] / (time.time() - t0) / 1e9, 1)]
    say(**row)
    # pipeline restricted to port subsets; staging on the NIC's NUMA node
    S = {p: pin(NICN[p], 24 * CH) for p in range(4)}; Sreg = {p: rdma.Region(p, S[p].data_ptr(), 24 * CH, False) for p in range(4)}
    big = torch.empty(n, dtype=torch.uint8, device=dev)
    for ports in ([0, 1, 2, 3], [1, 2, 3], [0, 2, 3], [1, 3]):
        free = {p: queue.Queue() for p in ports}; [free[p].put(s) for p in ports for s in range(24)]
        lq = queue.Queue(); lock = threading.Lock(); best = 0
        for rep in range(3):
            offs = list(range(0, n, CH))
            def puller(p):
                while True:
                    with lock:
                        if not offs: return
                        off = offs.pop()
                    m = min(CH, n - off); s = free[p].get()
                    conns[p].read(info["addr"] + off, info["rkeys"][str(p)], S[p].data_ptr() + s * CH, Sreg[p].lkey, m, 4 * M, 8)
                    lq.put((p, s, off, m))
            def lander():
                while True:
                    it = lq.get()
                    if it is None: return
                    batch = [it]
                    while len(batch) < 16:
                        try: x = lq.get_nowait()
                        except queue.Empty: break
                        if x is None: lq.put(None); break
                        batch.append(x)
                    for p, s, off, m in batch: big[off:off + m].copy_(S[p][s * CH:s * CH + m], non_blocking=True)
                    torch.cuda.synchronize(dev)
                    for p, s, off, m in batch: free[p].put(s)
            pt = [threading.Thread(target=puller, args=(p,)) for p in ports]; lt = threading.Thread(target=lander)
            t0 = time.time(); [x.start() for x in pt]; lt.start(); [x.join() for x in pt]; lq.put(None); lt.join()
            best = max(best, n / (time.time() - t0) / 1e9)
        say(card=c, pipeline_ports=ports, GBps=round(best, 1))
    del S, Sreg, big, B, g; torch.cuda.empty_cache()
