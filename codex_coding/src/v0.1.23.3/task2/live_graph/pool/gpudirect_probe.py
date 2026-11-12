"""Can the HCA read/write device memory directly (no host staging)? Register a GPU tensor's
address as an RDMA region and pull into it. HSA_FORCE_FINE_GRAIN_PCIE=1 maps device memory through
the PCIe BAR, which is what RCCL's net plugin relies on.
  gpudirect_probe.py <src.json> <my ib0 ip> <out.log> [cards]"""
import json, os, sys, threading, time
import torch
from fluidckpt.live import rdma
M = 1 << 20; G = 1 << 30; NIC = [0, 1, 4, 5]; GN = {0: 0, 1: 3, 2: 2, 3: 1, 4: 4, 5: 7, 6: 6, 7: 5}
info = json.load(open(sys.argv[1])); me = sys.argv[2]; out = open(sys.argv[3], "a")
cards = [int(x) for x in (sys.argv[4] if len(sys.argv) > 4 else "1").split(",")]
def say(**k):
    line = json.dumps(k); out.write(line + "\n"); out.flush(); print(line, flush=True)
say(env_fine_grain=os.environ.get("HSA_FORCE_FINE_GRAIN_PCIE"))
conns = {p: [rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(info["ip"], p), info["port"]) for _ in range(len(cards))] for p in range(4)}
g, reg = {}, {}
for c in cards:
    g[c] = torch.zeros(4 * G, dtype=torch.uint8, device=f"cuda:{c}"); torch.cuda.synchronize(f"cuda:{c}")
    reg[c] = {}
    for p in range(4):
        try:
            t = time.time(); reg[c][p] = rdma.Region(p, g[c].data_ptr(), 4 * G, False)
            if p == 0: say(test="ibv_reg_mr on device memory", card=c, ok=True, s=round(time.time() - t, 4))
        except Exception as e:
            say(test="ibv_reg_mr on device memory", card=c, port=p, ok=False, error=repr(e)); sys.exit(0)
c0 = cards[0]; r = info["regions"]["1"]
conns[1][0].read(r["addr"], r["rkeys"][1], g[c0].data_ptr(), reg[c0][1].lkey, 2 * G, 4 * M, 8)
torch.cuda.synchronize(f"cuda:{c0}")
x = g[c0][:2 * G]
say(test="bytes landed in HBM", all_sevens=bool((x[::4099] == 7).all()), first=int(x[0]), last=int(x[-1]), untouched_half_is_zero=bool((g[c0][2 * G::4099] == 0).all()))
def run(tag, plan, secs=3.0):
    """plan: [(port, card, conn index)]; each stream pulls 2 GiB repeatedly straight into HBM."""
    stop = threading.Event(); n = [0] * len(plan)
    def one(i, p, c, ci):
        rr = info["regions"][str(NIC[p])]
        while not stop.is_set():
            conns[p][ci].read(rr["addr"], rr["rkeys"][p], g[c].data_ptr() + (i % 2) * 2 * G, reg[c][p].lkey, 2 * G, 4 * M, 8); n[i] += 2 * G
    th = [threading.Thread(target=one, args=(i, *x)) for i, x in enumerate(plan)]
    t0 = time.time(); [x.start() for x in th]; time.sleep(secs); stop.set(); [x.join() for x in th]; dt = time.time() - t0
    say(test="direct", case=tag, total_GBps=round(sum(n) / dt / 1e9, 1), per_stream=[round(v / dt / 1e9, 1) for v in n])
for p in range(4): run(f"port {p} -> card {c0}", [(p, c0, 0)])
run(f"4 ports -> card {c0}", [(p, c0, 0) for p in range(4)])
if len(cards) >= 2:
    run("4 ports -> 2 cards", [(p, cards[p % 2], 0) for p in range(4)])
if len(cards) >= 4:
    run("4 ports -> 4 cards (one each)", [(p, cards[p], 0) for p in range(4)])
    run("4 ports x 4 cards (16 streams)", [(p, c, i) for p in range(4) for i, c in enumerate(cards)])
