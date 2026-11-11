"""Why does RDMA->staging->H2D run at 28 GB/s when each half alone does 45+? Vary where the
staging memory lives (NUMA) and how many landers drain it. Source: rdma_bench.py src on a peer."""
import ctypes, json, queue, sys, threading, time
import torch
from fluidckpt.live import rdma
from fluidckpt.live.noded import prefer_numa
M = 1 << 20; G = 1 << 30
info = json.load(open(sys.argv[1])); me = sys.argv[2]; card = int(sys.argv[3])
dev = f"cuda:{card}"; n = info["nbytes"]; CH = 64 * M; SLOT = CH
gpu = torch.empty(n, dtype=torch.uint8, device=dev); gpu.zero_(); torch.cuda.synchronize(dev)
NIC_NUMA = [0, 1, 4, 5]
def make_pool(numas, slots_each):
    pools = []
    for nu in numas:
        prefer_numa(nu); t = torch.empty(slots_each * SLOT, dtype=torch.uint8, pin_memory=True); prefer_numa(None)
        regs = [rdma.Region(p, t.data_ptr(), t.numel(), False) for p in range(4)]
        pools.append((t, regs))
    return pools
conns = [rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(info["ip"], p), info["port"]) for p in range(4)]
def run(tag, pools, port_pool, do_rdma=True, do_h2d=True, landers=1):
    # port_pool[p] -> index of the pool whose slots port p fills
    free = [queue.Queue() for _ in pools]
    for k, (t, _) in enumerate(pools):
        for s in range(t.numel() // SLOT): free[k].put(s)
    offs = list(range(0, n, CH)); lock = threading.Lock(); landq = queue.Queue(); done = [0]
    def puller(p):
        k = port_pool[p]; t, regs = pools[k]
        while True:
            with lock:
                if not offs: return
                off = offs.pop()
            m = min(CH, n - off); s = free[k].get()
            if do_rdma:
                conns[p].read(info["addr"] + off, info["rkeys"][str(p)], t.data_ptr() + s * SLOT, regs[p].lkey, m, 4 * M, 8)
            landq.put((k, s, off, m))
    def lander():
        while True:
            it = landq.get()
            if it is None: landq.put(None); return
            batch = [it]
            while len(batch) < 16:
                try: x = landq.get_nowait()
                except queue.Empty: break
                if x is None: landq.put(None); break
                batch.append(x)
            if do_h2d:
                for k, s, off, m in batch:
                    gpu[off:off + m].copy_(pools[k][0][s * SLOT:s * SLOT + m], non_blocking=True)
                torch.cuda.synchronize(dev)
            for k, s, off, m in batch: free[k].put(s)
    for rep in range(3):
        offs[:] = list(range(0, n, CH))
        pt = [threading.Thread(target=puller, args=(p,)) for p in range(4)]
        lt = [threading.Thread(target=lander) for _ in range(landers)]
        t0 = time.time(); [x.start() for x in pt + lt]; [x.join() for x in pt]; landq.put(None); [x.join() for x in lt]
        dt = time.time() - t0
        while not landq.empty(): landq.get()
        print(json.dumps({"case": tag, "rep": rep, "GBps": round(n / dt / 1e9, 2), "s": round(dt, 3)}), flush=True)
one = make_pool([0], 96)
run("rdma_only numa0", one, [0, 0, 0, 0], do_h2d=False)
run("h2d_only numa0", one, [0, 0, 0, 0], do_rdma=False)
run("both numa0 1lander", one, [0, 0, 0, 0])
run("both numa0 2landers", one, [0, 0, 0, 0], landers=2)
del one
nic = make_pool(NIC_NUMA, 24)
run("rdma_only nic-local", nic, [0, 1, 2, 3], do_h2d=False)
run("h2d_only nic-local", nic, [0, 1, 2, 3], do_rdma=False)
run("both nic-local", nic, [0, 1, 2, 3])
del nic
far = make_pool([2, 3, 6, 7], 24)
run("both off-nic numas 2,3,6,7", far, [0, 1, 2, 3])
