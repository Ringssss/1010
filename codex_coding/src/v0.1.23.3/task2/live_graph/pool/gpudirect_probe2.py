"""Why does the daemon's direct path reach ~55 GB/s when the probe reached 187? Isolate:
fresh vs pre-touched device memory, 64 MiB calls vs one long call, one card at a time per port.
  gpudirect_probe2.py <src.json> <my ib0 ip> <out.log>"""
import json, sys, threading, time
import torch
from fluidckpt.live import rdma
M = 1 << 20; G = 1 << 30; NIC = [0, 1, 4, 5]
info = json.load(open(sys.argv[1])); me = sys.argv[2]; out = open(sys.argv[3], "a")
def say(**k):
    line = json.dumps(k); out.write(line + "\n"); out.flush(); print(line, flush=True)
conns = {p: rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(info["ip"], p), info["port"]) for p in range(4)}
cards = [1, 2, 5, 6]; N = 8 * G
def run(tag, touch, call_bytes, piece=4 * M, window=8, sync_each=False):
    t0 = time.time()
    g = {c: torch.empty(N, dtype=torch.uint8, device=f"cuda:{c}") for c in cards}
    if touch:
        for c in cards: g[c].zero_()
    for c in cards: torch.cuda.synchronize(f"cuda:{c}")
    t_alloc = time.time() - t0
    t0 = time.time(); reg = {c: rdma.Region(p, g[c].data_ptr(), N, False) for p, c in enumerate(cards)}; t_reg = time.time() - t0
    per = [0.0] * 4
    def one(p, c):
        t = time.time(); off = 0; k = 0
        while off < N:
            r = info["regions"][str((k + NIC[p]) % 8)]; m = min(call_bytes, N - off)
            conns[p].read(r["addr"] + (off % (2 * G - m) // m * m if m < 2 * G else 0), r["rkeys"][p], g[c].data_ptr() + off, reg[c].lkey, m, piece, window)
            off += m; k += 1
        per[p] = N / (time.time() - t) / 1e9
    th = [threading.Thread(target=one, args=(p, c)) for p, c in enumerate(cards)]
    t0 = time.time(); [x.start() for x in th]; [x.join() for x in th]
    for c in cards: torch.cuda.synchronize(f"cuda:{c}")
    dt = time.time() - t0
    ok = all(bool((g[c][::65537] == 7).all()) for c in cards)
    say(case=tag, total_GBps=round(4 * N / dt / 1e9, 1), per_port=[round(x, 1) for x in per], alloc_s=round(t_alloc, 3), reg_s=round(t_reg, 4), bytes_ok=ok)
    for r in reg.values(): r.close()
    del g; torch.cuda.empty_cache()
run("pre-touched, 2 GiB calls", True, 2 * G)
run("pre-touched, 64 MiB calls", True, 64 * M)
run("fresh (torch.empty), 2 GiB calls", False, 2 * G)
run("fresh (torch.empty), 64 MiB calls", False, 64 * M)
run("fresh, 64 MiB calls, 16 MiB pieces", False, 64 * M, piece=16 * M, window=4)
run("fresh, 64 MiB calls, 64 MiB single piece", False, 64 * M, piece=64 * M, window=1)
run("pre-touched, 64 MiB calls, 64 MiB single piece", True, 64 * M, piece=64 * M, window=1)
run("fresh again, 64 MiB calls (allocator reuse?)", False, 64 * M)
