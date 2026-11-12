"""Is the warm-up of direct RDMA tied to a RANGE of device memory (and does it survive free +
re-allocate)? If so, a daemon that owns a long-lived, pre-warmed HBM arena per card always
lands restores at line rate.  gpudirect_probe12.py <my ib0 ip> <out.log> <src.json>"""
import json, sys, threading, time
import torch
from fluidckpt.live import rdma
M = 1 << 20; G = 1 << 30; NIC = [0, 1, 4, 5]
me = sys.argv[1]; out = open(sys.argv[2], "a"); info = json.load(open(sys.argv[3]))
def say(**k):
    line = json.dumps(k); out.write(line + "\n"); out.flush(); print(line, flush=True)
A = [(0, 2), (1, 1), (2, 6), (3, 5)]; call = 64 * M
conns = {p: rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(info["ip"], p), info["port"]) for p in range(4)}
def traffic(tag, g, reg, lo, hi, secs):
    stop = threading.Event(); cnt = [0] * 4
    def one(i, p, c):
        j = 0; span = hi - lo
        while not stop.is_set():
            r = info["regions"][str(NIC[p])]
            conns[p].read(r["addr"] + (j % 32) * call, r["rkeys"][p], g[c].data_ptr() + lo + (j * call) % span, reg[(c, p)].lkey, call, 4 * M, 8)
            cnt[i] += call; j += 1
    th = [threading.Thread(target=one, args=(i, p, c)) for i, (p, c) in enumerate(A)]
    [x.start() for x in th]; last = 0; t = time.time(); rows = []
    for s in range(secs):
        time.sleep(1.0); now = sum(cnt); t2 = time.time(); rows.append(round((now - last) / (t2 - t) / 1e9)); last, t = now, t2
    stop.set(); [x.join() for x in th]
    say(case=tag, range_GiB=[lo // G, hi // G], GBps_each_second=rows)
def alloc(gib):
    g = {c: torch.empty(gib * G, dtype=torch.uint8, device=f"cuda:{c}") for _, c in A}
    return g, {(c, p): rdma.Region(p, g[c].data_ptr(), gib * G, False) for _, c in A for p in range(4)}, {c: hex(g[c].data_ptr()) for _, c in A}
def free(g, reg):
    for r in reg.values(): r.close()
    g.clear(); torch.cuda.empty_cache()
g, reg, ptr = alloc(8); say(case="alloc 8 GiB", ptr=ptr)
traffic("first buffer, from cold", g, reg, 0, 8 * G, 45)
free(g, reg); g, reg, ptr = alloc(8); say(case="freed and allocated 8 GiB again", ptr=ptr)
traffic("same size again", g, reg, 0, 8 * G, 6)
free(g, reg); g, reg, ptr = alloc(24); say(case="freed and allocated 24 GiB", ptr=ptr)
traffic("24 GiB buffer: its first 8 GiB", g, reg, 0, 8 * G, 6)
traffic("24 GiB buffer: bytes 8-24 GiB (new range?)", g, reg, 8 * G, 24 * G, 30)
traffic("24 GiB buffer: first 8 GiB again", g, reg, 0, 8 * G, 4)
time.sleep(30)
traffic("after 30 s idle: whole 24 GiB", g, reg, 0, 24 * G, 6)
free(g, reg); g, reg, ptr = alloc(40); say(case="freed and allocated 40 GiB", ptr=ptr)
traffic("40 GiB buffer: bytes 24-40 GiB", g, reg, 24 * G, 40 * G, 20)
traffic("40 GiB buffer: whole", g, reg, 0, 40 * G, 6)
