"""One factor at a time around the configuration that reached ~185 GB/s (pairing A, 4 ports).
  gpudirect_probe6.py <my ib0 ip> <out.log> <src.json> <flags>
flags (comma separated): cards4 | ownmr | onepass | empty | pairB | n16 | contexts8"""
import json, sys, threading, time
import torch
from fluidckpt.live import rdma
M = 1 << 20; G = 1 << 30; NIC = [0, 1, 4, 5]
me = sys.argv[1]; out = open(sys.argv[2], "a"); info = json.load(open(sys.argv[3]))
F = set(sys.argv[4].split(",")) if len(sys.argv) > 4 and sys.argv[4] else set()
pairs = [(0, 1), (1, 2), (2, 5), (3, 6)] if "pairB" in F else [(0, 2), (1, 1), (2, 6), (3, 5)]
C = sorted(c for _, c in pairs) if "cards4" in F else list(range(8))
N = (16 if "n16" in F else 8) * G; call = 64 * M
if "contexts8" in F:
    for c in range(8): torch.zeros(1, device=f"cuda:{c}")
g = {c: (torch.empty if "empty" in F else torch.zeros)(N, dtype=torch.uint8, device=f"cuda:{c}") for c in C}
for c in C: torch.cuda.synchronize(f"cuda:{c}")
reg = {(c, p): rdma.Region(p, g[c].data_ptr(), N, False) for c in C for p in range(4) if "ownmr" not in F or (p, c) in pairs}
conns = {p: rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(info["ip"], p), info["port"]) for p in range(4)}
stop = threading.Event(); res = {}
def one(p, c):
    n = 0; j = 0; t = time.time()
    while not stop.is_set() and not ("onepass" in F and n >= N):
        r = info["regions"][str(NIC[p])]
        conns[p].read(r["addr"] + (j * call) % (2 * G), r["rkeys"][p], g[c].data_ptr() + (j * call) % N, reg[(c, p)].lkey, call, 4 * M, 8)
        n += call; j += 1
    res[p] = n / (time.time() - t) / 1e9
th = [threading.Thread(target=one, args=x) for x in pairs]
[x.start() for x in th]
if "onepass" not in F:
    time.sleep(3.0); stop.set()
[x.join() for x in th]
line = json.dumps({"flags": sorted(F), "total_GBps": round(sum(res.values()), 1), "per_port": [round(res[p], 1) for p, _ in pairs]})
out.write(line + "\n"); print(line, flush=True)
