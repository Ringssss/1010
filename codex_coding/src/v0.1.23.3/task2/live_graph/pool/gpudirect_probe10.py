"""The fast state of direct RDMA into device memory: how long does it last when idle, and what
restores it (traffic on the same ports to other cards? on some ports only? device activity)?
  gpudirect_probe10.py <my ib0 ip> <out.log> <src.json>"""
import json, sys, threading, time
import torch
from fluidckpt.live import rdma
M = 1 << 20; G = 1 << 30; NIC = [0, 1, 4, 5]
me = sys.argv[1]; out = open(sys.argv[2], "a"); info = json.load(open(sys.argv[3]))
def say(**k):
    k["t"] = round(time.time() - T0, 1); line = json.dumps(k); out.write(line + "\n"); out.flush(); print(line, flush=True)
T0 = time.time()
A = [(0, 2), (1, 1), (2, 6), (3, 5)]; S = [(0, 0), (1, 3), (2, 4), (3, 7)]; N = 4 * G; call = 64 * M
g = {c: torch.zeros(N, dtype=torch.uint8, device=f"cuda:{c}") for c in range(8)}
reg = {(c, p): rdma.Region(p, g[c].data_ptr(), N, False) for c in range(8) for p in range(4)}
conns = {p: rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(info["ip"], p), info["port"]) for p in range(4)}
host = torch.empty(G, dtype=torch.uint8, pin_memory=True)
def stream(pairs, secs=None):
    res = {}
    end = None if secs is None else time.time() + secs
    def one(p, c):
        t = time.time(); off = 0; j = 0
        while (off < N) if end is None else (time.time() < end):
            r = info["regions"][str(NIC[p])]
            conns[p].read(r["addr"] + (j * call) % (2 * G), r["rkeys"][p], g[c].data_ptr() + off % N, reg[(c, p)].lkey, call, 4 * M, 8)
            off += call; j += 1
        res[p] = off / (time.time() - t) / 1e9
    th = [threading.Thread(target=one, args=x) for x in pairs]; [x.start() for x in th]; [x.join() for x in th]
    return [round(res[p], 1) for p, _ in pairs]
def probe(tag):
    r = stream(A); say(case=tag, total=round(sum(r), 1), per_port=r)
    return sum(r)
probe("at start (before any warm-up)")
stream(A, 8.0); probe("right after 8 s of traffic on the same pairs")
for idle in (20, 40, 80, 160):
    time.sleep(idle); v = probe(f"after {idle} s idle (since the previous probe)")
time.sleep(160); probe("after another 160 s idle")
stream(S, 8.0); probe("after 8 s of traffic on the same PORTS but to OTHER cards")
time.sleep(240); probe("after 240 s idle")
stream(A[:2], 8.0); probe("after 8 s of traffic on ports 0,1 only")
time.sleep(240); probe("after 240 s idle")
def h2d(c, secs):
    end = time.time() + secs; s = torch.empty(G, dtype=torch.uint8, device=f"cuda:{c}")
    while time.time() < end:
        s.copy_(host, non_blocking=True); torch.cuda.synchronize(f"cuda:{c}")
th = [threading.Thread(target=h2d, args=(c, 8.0)) for _, c in A]; [x.start() for x in th]; [x.join() for x in th]
probe("after 8 s of H2D into the target cards (no RDMA)")
