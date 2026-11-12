"""Why is network->staging->HBM stuck at ~35 GB/s for any number of cards when RDMA alone does
132 and H2D alone does 47 per card? Run the two halves at the same time on DIFFERENT memory and
see which one gives way, varying which ports receive and where the H2D source memory lives.
  land_probe5.py <src.json> <my ib0 ip> <out.log>"""
import ctypes, json, sys, threading, time
import torch
from fluidckpt.live import rdma
from fluidckpt.live.noded import prefer_numa
M = 1 << 20; G = 1 << 30; CH = 64 * M; NIC = [0, 1, 4, 5]
info = json.load(open(sys.argv[1])); me = sys.argv[2]; out = open(sys.argv[3], "a")
def say(**k):
    line = json.dumps(k); out.write(line + "\n"); out.flush(); print(line, flush=True)
def pin(numa, n):
    prefer_numa(numa, strict=True); t = torch.empty(n, dtype=torch.uint8, pin_memory=True); prefer_numa(None); t.fill_(3); return t
S = {p: pin(NIC[p], 2 * G) for p in range(4)}                      # RDMA lands here
Sreg = {p: rdma.Region(p, S[p].data_ptr(), 2 * G, False) for p in range(4)}
T = {j: pin(j, 2 * G) for j in range(8)}                           # H2D reads from here
conns = {p: rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(info["ip"], p), info["port"]) for p in range(4)}
cards = [1, 2, 5, 6]
gp = {c: torch.empty(2 * G, dtype=torch.uint8, device=f"cuda:{c}") for c in cards}
for c in cards: gp[c].zero_(); torch.cuda.synchronize(f"cuda:{c}")
def run(tag, ports=(), h2d=(), src_numas=NIC, cpu_writers=0, secs=3.0, same_memory=False):
    stop = threading.Event(); cnt = {"rdma": 0, "h2d": 0, "cpu": 0}; lock = threading.Lock()
    def puller(p):
        r = info["regions"][str(NIC[p])]; n = 0
        while not stop.is_set():
            conns[p].read(r["addr"], r["rkeys"][p], S[p].data_ptr(), Sreg[p].lkey, 2 * G, 4 * M, 8); n += 2 * G
        with lock: cnt["rdma"] += n
    def lander(c, i):
        dev = f"cuda:{c}"; n = 0
        srcs = [S[p] for p in range(4)] if same_memory else [T[j] for j in src_numas]
        k = i
        while not stop.is_set():
            src = srcs[k % len(srcs)]; k += 1
            for off in range(0, 2 * G, CH): gp[c][off:off + CH].copy_(src[off:off + CH], non_blocking=True)
            torch.cuda.synchronize(dev); n += 2 * G
        with lock: cnt["h2d"] += n
    def writer(i):
        p = i % 4; a = S[p].data_ptr(); b = T[(i * 3 + 2) % 8].data_ptr(); n = 0
        while not stop.is_set():
            ctypes.memmove(a + (i // 4) * (G // 2), b, G // 2); n += G // 2
        with lock: cnt["cpu"] += n
    th = [threading.Thread(target=puller, args=(p,)) for p in ports] + \
         [threading.Thread(target=lander, args=(c, i)) for i, c in enumerate(h2d)] + \
         [threading.Thread(target=writer, args=(i,)) for i in range(cpu_writers)]
    t0 = time.time(); [x.start() for x in th]; time.sleep(secs); stop.set(); [x.join() for x in th]; dt = time.time() - t0
    say(case=tag, ports=list(ports), h2d_cards=list(h2d), rdma_GBps=round(cnt["rdma"] / dt / 1e9, 1),
        h2d_GBps=round(cnt["h2d"] / dt / 1e9, 1), cpu_copy_GBps=round(cnt["cpu"] / dt / 1e9, 1))
run("H2D alone, 1 card", h2d=[1])
run("H2D alone, 2 cards", h2d=[1, 2])
run("H2D alone, 4 cards", h2d=[1, 2, 5, 6])
run("RDMA alone, 4 ports", ports=[0, 1, 2, 3])
run("both: 4 ports + 1 card (different memory, same NUMA nodes)", ports=[0, 1, 2, 3], h2d=[1])
run("both: 4 ports + 2 cards", ports=[0, 1, 2, 3], h2d=[1, 2])
run("both: 4 ports + 4 cards", ports=[0, 1, 2, 3], h2d=[1, 2, 5, 6])
run("both: 4 ports + 2 cards, H2D source on NUMA 2,3,6,7 (no NIC there)", ports=[0, 1, 2, 3], h2d=[1, 2], src_numas=[2, 3, 6, 7])
run("both: 1 port + 2 cards", ports=[1], h2d=[1, 2])
run("both: 2 ports + 2 cards", ports=[1, 2], h2d=[1, 2])
run("both: 2 ports (socket 1 only) + 2 cards on socket 0, source on socket 0", ports=[2, 3], h2d=[1, 2], src_numas=[2, 3])
run("both: 2 ports (socket 0 only) + 2 cards on socket 1, source on socket 1", ports=[0, 1], h2d=[5, 6], src_numas=[6, 7])
run("both: 4 ports + 2 cards, H2D reads the memory RDMA is writing", ports=[0, 1, 2, 3], h2d=[1, 2], same_memory=True)
run("CPU memcpy writers (8) + 2 cards, no network", h2d=[1, 2], cpu_writers=8)
