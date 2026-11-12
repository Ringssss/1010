"""Direct RDMA into device memory: does the size of the registered region / the amount of
distinct device memory written change the rate? Pairing A (each port on a near card).
  gpudirect_probe5.py <my ib0 ip> <out.log> <src.json>"""
import json, sys, threading, time
import torch
from fluidckpt.live import rdma
M = 1 << 20; G = 1 << 30; NIC = [0, 1, 4, 5]
me = sys.argv[1]; out = open(sys.argv[2], "a"); info = json.load(open(sys.argv[3]))
def say(**k):
    line = json.dumps(k); out.write(line + "\n"); out.flush(); print(line, flush=True)
A = [(0, 2), (1, 1), (2, 6), (3, 5)]
conns = {p: rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(info["ip"], p), info["port"]) for p in range(4)}
def run(tag, buf_gib, mr_gib, touch, call=64 * M, fill_gib=None):
    N = buf_gib * G; fill = (fill_gib or buf_gib) * G
    t0 = time.time()
    g = {c: (torch.zeros if touch else torch.empty)(N, dtype=torch.uint8, device=f"cuda:{c}") for _, c in A}
    for _, c in A: torch.cuda.synchronize(f"cuda:{c}")
    t_alloc = time.time() - t0
    t0 = time.time()
    regs = {(c, k): rdma.Region(p, g[c].data_ptr() + k * mr_gib * G, min(mr_gib * G, N - k * mr_gib * G), False)
            for p, c in A for k in range(-(-buf_gib // mr_gib))}
    t_reg = time.time() - t0
    res = {}
    def one(p, c):
        r = info["regions"][str(NIC[p])]; off = 0; j = 0; t = time.time()
        while off < fill:
            k = off // (mr_gib * G)
            conns[p].read(r["addr"] + (j * call) % (2 * G - call), r["rkeys"][p], g[c].data_ptr() + off, regs[(c, k)].lkey, call, 4 * M, 8)
            off += call; j += 1
        res[p] = fill / (time.time() - t) / 1e9
    th = [threading.Thread(target=one, args=x) for x in A]
    t0 = time.time(); [x.start() for x in th]; [x.join() for x in th]; dt = time.time() - t0
    say(case=tag, buf_GiB=buf_gib, mr_GiB=mr_gib, touched=touch, total_GBps=round(4 * fill / dt / 1e9, 1),
        per_port=[round(res[p], 1) for p, _ in A], alloc_s=round(t_alloc, 3), reg_s=round(t_reg, 4))
    for r in regs.values(): r.close()
    del g; torch.cuda.empty_cache()
run("8 GiB buffer, one MR, touched", 8, 8, True)
if len(sys.argv) > 4: sys.exit(0)
run("16 GiB buffer, one MR, touched", 16, 16, True)
run("32 GiB buffer, one MR, touched", 32, 32, True)
run("32 GiB buffer, one MR, fresh", 32, 32, False)
run("32 GiB buffer, 4 GiB MRs, fresh", 32, 4, False)
run("32 GiB buffer, 1 GiB MRs, fresh", 32, 1, False)
run("32 GiB buffer, one MR, fresh, only first 8 GiB written", 32, 32, False, fill_gib=8)
run("16 GiB buffer, one MR, fresh", 16, 16, False)
run("8 GiB buffer, one MR, fresh", 8, 8, False)
