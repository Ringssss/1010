"""What makes a device page 'warm' for the HCA, and how long does it stay warm?
Pairing A, 8 GiB per card, each pass writes every page exactly once.
  gpudirect_probe7.py <my ib0 ip> <out.log> <src.json>"""
import ctypes, json, mmap, sys, threading, time
import torch
from fluidckpt.live import rdma
M = 1 << 20; G = 1 << 30; NIC = [0, 1, 4, 5]
me = sys.argv[1]; out = open(sys.argv[2], "a"); info = json.load(open(sys.argv[3]))
def say(**k):
    line = json.dumps(k); out.write(line + "\n"); out.flush(); print(line, flush=True)
A = [(0, 2), (1, 1), (2, 6), (3, 5)]; N = 8 * G; call = 64 * M
for c in range(8): torch.zeros(1, device=f"cuda:{c}")
conns = {p: rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(info["ip"], p), info["port"]) for p in range(4)}
def alloc(): return {c: torch.zeros(N, dtype=torch.uint8, device=f"cuda:{c}") for _, c in A}
def register(g): return {(c, p): rdma.Region(p, g[c].data_ptr(), N, False) for _, c in A for p in range(4)}
def onepass(tag, g, reg, pairs=A, lo=0, hi=N):
    res = {}
    def one(p, c):
        t = time.time(); off = lo; j = 0
        while off < hi:
            r = info["regions"][str(NIC[p])]
            conns[p].read(r["addr"] + (j * call) % (2 * G), r["rkeys"][p], g[c].data_ptr() + off, reg[(c, p)].lkey, call, 4 * M, 8)
            off += call; j += 1
        res[p] = (hi - lo) / (time.time() - t) / 1e9
    th = [threading.Thread(target=one, args=x) for x in pairs]
    t0 = time.time(); [x.start() for x in th]; [x.join() for x in th]; dt = time.time() - t0
    say(case=tag, total_GBps=round(len(pairs) * (hi - lo) / dt / 1e9, 1), per_port=[round(res[p], 1) for p, _ in pairs], s=round(dt, 3))
g = alloc(); reg = register(g)
onepass("pass 1: fresh pages", g, reg)
onepass("pass 2: same pages, same ports", g, reg)
onepass("pass 3: same pages again", g, reg)
onepass("pass 4: same pages, the OTHER near port (0<->... swap within socket)", g, reg, pairs=[(1, 2), (0, 1), (3, 6), (2, 5)])
onepass("pass 5: back to the first ports", g, reg)
for c in g: g[c].fill_(1); torch.cuda.synchronize(f"cuda:{c}")
onepass("pass 6: after the GPU itself rewrote the buffer (fill_)", g, reg)
for r in reg.values(): r.close()
reg = register(g)
onepass("pass 7: after dereg + re-register of the same tensor", g, reg)
onepass("pass 8: again", g, reg)
for r in reg.values(): r.close()
del g; torch.cuda.empty_cache()
g = alloc(); reg = register(g)
onepass("pass 9: tensor freed (empty_cache) and allocated again", g, reg)
onepass("pass 10: again", g, reg)
# warming without a peer: loopback RDMA from this node's own host memory
mm = mmap.mmap(-1, 2 * G); la = ctypes.addressof(ctypes.c_char.from_buffer(mm)); ctypes.memset(la, 7, 2 * G)
lreg = [rdma.Region(p, la, 2 * G, True) for p in range(4)]
for p in range(4): rdma.serve(rdma.port_ip(me, p), 18830)
lo = {p: rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(me, p), 18830) for p in range(4)}
for r in reg.values(): r.close()
del g; torch.cuda.empty_cache()
N2 = 8 * G
g = {c: torch.empty(N2, dtype=torch.uint8, device=f"cuda:{c}") for _, c in A}; reg = register(g)
def warm(p, c):
    off = 0
    while off < N2:
        lo[p].read(la, lreg[p].rkey, g[c].data_ptr() + off, reg[(c, p)].lkey, min(2 * G, N2 - off), 4 * M, 8); off += 2 * G
th = [threading.Thread(target=warm, args=x) for x in A]
t0 = time.time(); [x.start() for x in th]; [x.join() for x in th]; dt = time.time() - t0
say(case="warm-up by loopback RDMA from own host memory", total_GBps=round(4 * N2 / dt / 1e9, 1), s=round(dt, 3))
onepass("pass 11: after loopback warm-up (fresh tensor)", g, reg)
