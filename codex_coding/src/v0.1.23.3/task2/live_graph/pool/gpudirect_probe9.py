"""Is the 5 s ramp of direct RDMA a device power state? One pass of 8 GiB per card (pairing A),
started cold, after keeping the cards busy, and while they are kept busy.
  gpudirect_probe9.py <my ib0 ip> <out.log> <src.json>"""
import json, sys, threading, time
import torch
from fluidckpt.live import rdma
M = 1 << 20; G = 1 << 30; NIC = [0, 1, 4, 5]
me = sys.argv[1]; out = open(sys.argv[2], "a"); info = json.load(open(sys.argv[3]))
def say(**k):
    line = json.dumps(k); out.write(line + "\n"); out.flush(); print(line, flush=True)
A = [(0, 2), (1, 1), (2, 6), (3, 5)]; N = 8 * G; call = 64 * M
g = {c: torch.zeros(N, dtype=torch.uint8, device=f"cuda:{c}") for _, c in A}
reg = {(c, p): rdma.Region(p, g[c].data_ptr(), N, False) for _, c in A for p in range(4)}
conns = {p: rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(info["ip"], p), info["port"]) for p in range(4)}
host = {c: torch.empty(G, dtype=torch.uint8, pin_memory=True) for _, c in A}
def onepass(tag):
    res = {}
    def one(p, c):
        t = time.time(); off = 0; j = 0
        while off < N:
            r = info["regions"][str(NIC[p])]
            conns[p].read(r["addr"] + (j * call) % (2 * G), r["rkeys"][p], g[c].data_ptr() + off, reg[(c, p)].lkey, call, 4 * M, 8)
            off += call; j += 1
        res[p] = N / (time.time() - t) / 1e9
    th = [threading.Thread(target=one, args=x) for x in A]
    t0 = time.time(); [x.start() for x in th]; [x.join() for x in th]; dt = time.time() - t0
    say(case=tag, total_GBps=round(4 * N / dt / 1e9, 1), per_port=[round(res[p], 1) for p, _ in A], s=round(dt, 3))
stop = threading.Event()
def spin(kind):
    def compute(c):
        x = torch.rand(1 << 22, device=f"cuda:{c}")
        while not stop.is_set():
            for _ in range(50): x = torch.sin(x) + 1e-3
            torch.cuda.synchronize(f"cuda:{c}")
    def h2d(c):
        s = torch.empty(G, dtype=torch.uint8, device=f"cuda:{c}")
        while not stop.is_set():
            s.copy_(host[c], non_blocking=True); torch.cuda.synchronize(f"cuda:{c}")
    def d2d(c):
        s = torch.empty(G, dtype=torch.uint8, device=f"cuda:{c}"); d = torch.empty(G, dtype=torch.uint8, device=f"cuda:{c}")
        while not stop.is_set():
            d.copy_(s); torch.cuda.synchronize(f"cuda:{c}")
    fn = {"compute": compute, "h2d": h2d, "d2d": d2d}[kind]
    th = [threading.Thread(target=fn, args=(c,)) for _, c in A]; [x.start() for x in th]; return th
time.sleep(6); onepass("cold (cards idle for 6 s)")
time.sleep(6); onepass("cold again")
for kind in ("compute", "d2d", "h2d"):
    time.sleep(6)
    stop.clear(); th = spin(kind); time.sleep(6)
    onepass(f"while the cards run {kind} (started 6 s earlier)")
    stop.set(); [x.join() for x in th]
    onepass(f"right after {kind} stopped")
    time.sleep(1.0); onepass("1 s later")
    time.sleep(3.0); onepass("4 s later")
# does traffic itself keep it up? ramp with RDMA for 7 s, then pauses of growing length
def ramp(secs):
    end = time.time() + secs
    def one(p, c):
        j = 0
        while time.time() < end:
            r = info["regions"][str(NIC[p])]
            conns[p].read(r["addr"], r["rkeys"][p], g[c].data_ptr() + (j * call) % N, reg[(c, p)].lkey, call, 4 * M, 8); j += 1
    th = [threading.Thread(target=one, args=x) for x in A]; [x.start() for x in th]; [x.join() for x in th]
for pause in (0.0, 0.2, 1.0, 3.0, 10.0):
    ramp(7.0); time.sleep(pause); onepass(f"after a 7 s RDMA ramp and a {pause} s pause")
