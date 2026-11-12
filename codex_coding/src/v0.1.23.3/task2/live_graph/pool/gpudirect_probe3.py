"""Attribute the direct-path limit: is it distinct SOURCE pages, distinct DESTINATION (device)
pages, or the port? 4 ports -> 4 cards, 8 GiB per card.
  gpudirect_probe3.py <my ib0 ip> <out.log> <src.json> [more src.json ...]"""
import json, sys, threading, time
import torch
from fluidckpt.live import rdma
M = 1 << 20; G = 1 << 30; NIC = [0, 1, 4, 5]
me = sys.argv[1]; out = open(sys.argv[2], "a"); infos = [json.load(open(f)) for f in sys.argv[3:]]
def say(**k):
    line = json.dumps(k); out.write(line + "\n"); out.flush(); print(line, flush=True)
cards = [1, 2, 5, 6]; N = 8 * G; CH = 64 * M
g = {c: torch.zeros(N, dtype=torch.uint8, device=f"cuda:{c}") for c in cards}
reg = {(c, p): rdma.Region(p, g[c].data_ptr(), N, False) for c in cards for p in range(4)}
conns = {(s, p, k): rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(info["ip"], p), info["port"])
         for s, info in enumerate(infos) for p in range(4) for k in range(2)}
def run(tag, src_distinct, dst_distinct, streams_per_port=1, nsrc=1, secs=2.5):
    stop = threading.Event(); cnt = []
    def one(i, p, c, s, k):
        info = infos[s]; n = 0; j = i
        while not stop.is_set():
            r = info["regions"][str((j + NIC[p]) % 8 if src_distinct else NIC[p])]
            so = (j * CH) % (2 * G - CH) if src_distinct else 0
            do = ((j * streams_per_port + (i % streams_per_port)) * CH) % (N - CH) if dst_distinct else (i % streams_per_port) * CH
            conns[(s, p, k)].read(r["addr"] + so, r["rkeys"][p], g[c].data_ptr() + do, reg[(c, p)].lkey, CH, 4 * M, 8)
            n += CH; j += 1
        cnt.append((p, n))
    th = []
    for p, c in enumerate(cards):
        for q in range(streams_per_port):
            th.append(threading.Thread(target=one, args=(len(th), p, c, q % nsrc, q // nsrc)))
    t0 = time.time(); [x.start() for x in th]; time.sleep(secs); stop.set(); [x.join() for x in th]; dt = time.time() - t0
    per = [0.0] * 4
    for p, n in cnt: per[p] += n / dt / 1e9
    say(case=tag, total_GBps=round(sum(per), 1), per_port=[round(x, 1) for x in per])
run("same 64 MiB source, same 64 MiB of device memory", False, False)
run("distinct source pages (16 GiB), same device memory", True, False)
run("same source, distinct device memory (8 GiB/card)", False, True)
run("distinct source, distinct device memory", True, True)
if len(infos) > 1:
    run(f"distinct/distinct, {len(infos)} source nodes, one stream each per port", True, True, streams_per_port=len(infos), nsrc=len(infos))
run("distinct/distinct, 2 streams per port from one node", True, True, streams_per_port=2, nsrc=1)
