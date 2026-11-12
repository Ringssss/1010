"""Is the slow start of direct RDMA a property of a FRESH SOURCE (first reads of newly registered
source pages) rather than of the destination? The destination process stays the same; sources
are (a) a peer process that has been serving for a long time and (b) one started seconds ago.
For the fresh one: read every source page once (16 GiB distinct), then again, per second rates.
  gpudirect_probe11.py <my ib0 ip> <out.log> <old_src.json> <fresh_src.json>"""
import json, sys, threading, time
import torch
from fluidckpt.live import rdma
M = 1 << 20; G = 1 << 30; NIC = [0, 1, 4, 5]
me = sys.argv[1]; out = open(sys.argv[2], "a")
def say(**k):
    line = json.dumps(k); out.write(line + "\n"); out.flush(); print(line, flush=True)
A = [(0, 2), (1, 1), (2, 6), (3, 5)]; N = 8 * G; call = 64 * M
g = {c: torch.zeros(N, dtype=torch.uint8, device=f"cuda:{c}") for _, c in A}
reg = {(c, p): rdma.Region(p, g[c].data_ptr(), N, False) for _, c in A for p in range(4)}
def run(tag, info, secs, regions_per_port):
    """regions_per_port: how many of the source's eight 2 GiB regions each port walks through."""
    conns = {p: rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(info["ip"], p), info["port"]) for p in range(4)}
    stop = threading.Event(); cnt = [0] * 4
    def one(i, p, c):
        j = 0
        while not stop.is_set():
            k = (2 * i + (j // 32) % regions_per_port) % 8          # ports use disjoint regions
            r = info["regions"][str(k)]
            conns[p].read(r["addr"] + (j % 32) * call, r["rkeys"][p], g[c].data_ptr() + (j * call) % N, reg[(c, p)].lkey, call, 4 * M, 8)
            cnt[i] += call; j += 1
    th = [threading.Thread(target=one, args=(i, p, c)) for i, (p, c) in enumerate(A)]
    [x.start() for x in th]; last = list(cnt); t = time.time(); rows = []
    for s in range(secs):
        time.sleep(1.0); now = list(cnt); t2 = time.time()
        rows.append(round(sum(now[i] - last[i] for i in range(4)) / (t2 - t) / 1e9, 1)); last, t = now, t2
    stop.set(); [x.join() for x in th]
    for c in conns.values(): c.close()
    say(case=tag, GBps_each_second=rows, GiB_read_per_port=round(cnt[0] / G, 1))
old = json.load(open(sys.argv[3])); fresh = json.load(open(sys.argv[4]))
run("long-lived source, 1 region per port (2 GiB re-read)", old, 4, 1)
run("FRESH source, 1 region per port (2 GiB re-read)", fresh, 10, 1)
run("FRESH source again, same regions", fresh, 4, 1)
run("FRESH source, the other region of each port (never read before)", fresh, 10, 2)
run("long-lived source again", old, 4, 1)
