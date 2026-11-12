"""Is the direct rate steady in time? 4 streams (pairing A) for 16 s, rate printed every second.
First 8 s: write across the whole 8 GiB of each card; last 8 s: stay inside the first 1 GiB.
  gpudirect_probe8.py <my ib0 ip> <out.log> <src.json>"""
import json, sys, threading, time
import torch
from fluidckpt.live import rdma
M = 1 << 20; G = 1 << 30; NIC = [0, 1, 4, 5]
me = sys.argv[1]; out = open(sys.argv[2], "a"); info = json.load(open(sys.argv[3]))
A = [(0, 2), (1, 1), (2, 6), (3, 5)]; N = 8 * G; call = 64 * M
g = {c: torch.zeros(N, dtype=torch.uint8, device=f"cuda:{c}") for c in range(8)}
reg = {(c, p): rdma.Region(p, g[c].data_ptr(), N, False) for c in range(8) for p in range(4)}
conns = {p: rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(info["ip"], p), info["port"]) for p in range(4)}
stop = threading.Event(); cnt = [0] * 4; window = [N]
def one(i, p, c):
    j = 0
    while not stop.is_set():
        r = info["regions"][str(NIC[p])]
        conns[p].read(r["addr"] + (j * call) % (2 * G), r["rkeys"][p], g[c].data_ptr() + (j * call) % window[0], reg[(c, p)].lkey, call, 4 * M, 8)
        cnt[i] += call; j += 1
th = [threading.Thread(target=one, args=(i, p, c)) for i, (p, c) in enumerate(A)]
[x.start() for x in th]
last = list(cnt); t = time.time()
for sec in range(16):
    time.sleep(1.0)
    if sec == 8: window[0] = G
    now = list(cnt); t2 = time.time()
    rates = [round((now[i] - last[i]) / (t2 - t) / 1e9, 1) for i in range(4)]
    line = json.dumps({"sec": sec, "window_GiB": window[0] / G, "total": round(sum(rates), 1), "per_port": rates, "written_GiB_per_card": round(now[0] / G, 1)})
    out.write(line + "\n"); print(line, flush=True); last, t = now, t2
stop.set(); [x.join() for x in th]
