"""Reproduce or refute the 187 GB/s direct figure: same pairing, vary call size and how much
device memory is written. 4 ports, one card each.
  gpudirect_probe4.py <my ib0 ip> <out.log> <src.json>"""
import json, sys, threading, time
import torch
from fluidckpt.live import rdma
M = 1 << 20; G = 1 << 30; NIC = [0, 1, 4, 5]
me = sys.argv[1]; out = open(sys.argv[2], "a"); info = json.load(open(sys.argv[3]))
def say(**k):
    line = json.dumps(k); out.write(line + "\n"); out.flush(); print(line, flush=True)
C = [0, 1, 2, 3, 4, 5, 6, 7]; N = 8 * G
g = {c: torch.zeros(N, dtype=torch.uint8, device=f"cuda:{c}") for c in C}
reg = {(c, p): rdma.Region(p, g[c].data_ptr(), N, False) for c in C for p in range(4)}
conns = {p: rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(info["ip"], p), info["port"]) for p in range(4)}
def run(tag, pairs, call, window_bytes, src_span=2 * G, secs=2.5, piece=4 * M, win=8):
    """pairs: [(port, card)]; each stream writes [0, window_bytes) of its card over and over in `call`-byte reads."""
    stop = threading.Event(); res = {}
    def one(p, c):
        n = 0; j = 0
        while not stop.is_set():
            r = info["regions"][str(NIC[p])]
            do = (j * call) % window_bytes if window_bytes > call else 0
            so = (j * call) % src_span if src_span > call else 0
            conns[p].read(r["addr"] + so, r["rkeys"][p], g[c].data_ptr() + do, reg[(c, p)].lkey, call, piece, win)
            n += call; j += 1
        res[p] = n
    th = [threading.Thread(target=one, args=x) for x in pairs]
    t0 = time.time(); [x.start() for x in th]; time.sleep(secs); stop.set(); [x.join() for x in th]; dt = time.time() - t0
    say(case=tag, pairs=[f"p{p}->c{c}" for p, c in pairs], call_MiB=call // M, dst_window_GiB=window_bytes / G,
        total_GBps=round(sum(res.values()) / dt / 1e9, 1), per_port=[round(res[p] / dt / 1e9, 1) for p, _ in pairs])
A = [(0, 2), (1, 1), (2, 6), (3, 5)]          # the pairing that gave 187 in gpudirect_matrix
B = [(0, 1), (1, 2), (2, 5), (3, 6)]          # the pairing of probe2/probe3
S = [(0, 0), (1, 3), (2, 4), (3, 7)]          # each port with the card on its own NUMA node
for name, pr in (("A", A), ("B", B), ("same-root", S)):
    run(f"{name}: 2 GiB calls into the same 2 GiB", pr, 2 * G, 2 * G)
    run(f"{name}: 2 GiB calls across 8 GiB", pr, 2 * G, 8 * G)
    run(f"{name}: 64 MiB calls into the same 2 GiB", pr, 64 * M, 2 * G)
    run(f"{name}: 64 MiB calls across 8 GiB", pr, 64 * M, 8 * G)
run("A: 64 MiB calls across 8 GiB, 1 MiB pieces window 16", A, 64 * M, 8 * G, piece=M, win=16)
run("A: 64 MiB calls across 8 GiB, 64 MiB single piece", A, 64 * M, 8 * G, piece=64 * M, win=1)
run("A: 256 MiB calls across 8 GiB", A, 256 * M, 8 * G)
run("A: 1 GiB calls across 8 GiB", A, G, 8 * G)
