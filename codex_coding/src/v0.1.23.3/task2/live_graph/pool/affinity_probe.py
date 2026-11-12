"""Does the NUMA node of the memory matter to the HCA port that moves it? (host memory only)
  src : affinity_probe.py src <out.json>      eight 2 GiB regions, one bound to each NUMA node,
                                               every region registered on all four ports
  dst : affinity_probe.py dst <src.json> <out.log>
        (1) matrix  : one port at a time, source NUMA k -> destination staging on NUMA j
        (2) together: all four ports at once, sources matched to their port's NUMA vs mismatched
Port p of this cluster sits on NUMA node [0, 1, 4, 5][p]."""
import ctypes, json, mmap, os, socket, sys, threading, time
sys.path.insert(0, "/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib")
from fluidckpt.live import rdma
G = 1 << 30; M = 1 << 20; NIC = [0, 1, 4, 5]
IP = {"m03r2n07": "12.11.3.27", "m03r2n08": "12.11.3.28", "m03r2n09": "12.11.3.29", "m03r2n10": "12.11.3.30"}
libc = ctypes.CDLL("libc.so.6", use_errno=True)
def bind(numa):
    if numa is None: libc.syscall(238, 0, None, 0)
    else: libc.syscall(238, 2, (ctypes.c_ulong * 2)(1 << numa, 0), 128)
def region(numa, n):
    bind(numa); m = mmap.mmap(-1, n); a = ctypes.addressof(ctypes.c_char.from_buffer(m)); ctypes.memset(a, 7, n); bind(None)
    return m, a
me = IP[socket.gethostname()]
if sys.argv[1] == "src":
    keep, info = [], {"ip": me, "port": 18820, "regions": {}}
    for k in range(8):
        m, a = region(k, 2 * G); regs = [rdma.Region(p, a, 2 * G, True) for p in range(4)]
        keep.append((m, regs)); info["regions"][k] = {"addr": a, "rkeys": [r.rkey for r in regs]}
    for p in range(4): rdma.serve(rdma.port_ip(me, p), 18820)
    json.dump(info, open(sys.argv[2] + ".tmp", "w")); os.replace(sys.argv[2] + ".tmp", sys.argv[2])
    time.sleep(float(sys.argv[3]) if len(sys.argv) > 3 else 300)
else:
    info = json.load(open(sys.argv[2])); out = open(sys.argv[3], "a")
    def say(**k):
        line = json.dumps(k); out.write(line + "\n"); out.flush(); print(line, flush=True)
    stage = {}
    for j in range(8):
        m, a = region(j, 2 * G); stage[j] = (m, a, [rdma.Region(p, a, 2 * G, False) for p in range(4)])
    conns = {p: [rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(info["ip"], p), info["port"]) for _ in range(2)] for p in range(4)}
    def pull(p, k, j, reps=3, c=0):
        r = info["regions"][str(k)]; _, a, regs = stage[j]; t = time.time()
        for _ in range(reps): conns[p][c].read(r["addr"], r["rkeys"][p], a, regs[p].lkey, 2 * G, 4 * M, 8)
        return reps * 2 * G / (time.time() - t) / 1e9
    for p in range(4):                                   # source NUMA sweep, staging on the port's NUMA
        pull(p, NIC[p], NIC[p], 1)
        say(test="src_numa", port=p, dst_numa=NIC[p], GBps={k: round(pull(p, k, NIC[p]), 1) for k in range(8)})
    for p in range(4):                                   # destination NUMA sweep, source on the port's NUMA
        say(test="dst_numa", port=p, src_numa=NIC[p], GBps={j: round(pull(p, NIC[p], j), 1) for j in range(8)})
    def together(tag, plan, reps=4):
        """plan: [(port, src_numa, dst_numa, conn)] all running at once."""
        res = [0.0] * len(plan)
        def one(i, p, k, j, c): res[i] = pull(p, k, j, reps, c)
        th = [threading.Thread(target=one, args=(i, *x)) for i, x in enumerate(plan)]
        t = time.time(); [x.start() for x in th]; [x.join() for x in th]; dt = time.time() - t
        say(test="together", case=tag, total_GBps=round(len(plan) * reps * 2 * G / dt / 1e9, 1), per_stream=[round(x, 1) for x in res])
    together("4 ports, src and dst on the port's NUMA", [(p, NIC[p], NIC[p], 0) for p in range(4)])
    together("4 ports, src on the far socket", [(p, NIC[(p + 2) % 4], NIC[p], 0) for p in range(4)])
    together("4 ports, src on non-NIC NUMA of the same socket", [(0, 2, 0, 0), (1, 3, 1, 0), (2, 6, 4, 0), (3, 7, 5, 0)])
    together("4 ports, dst on non-NIC NUMA of the same socket", [(0, 0, 2, 0), (1, 1, 3, 0), (2, 4, 6, 0), (3, 5, 7, 0)])
    together("4 ports, all sources on NUMA 0", [(p, 0, NIC[p], 0) for p in range(4)])
    together("4 ports x 2 conns, matched", [(p, NIC[p], NIC[p], c) for p in range(4) for c in range(2)])
    together("ports 1,2,3 matched (a card on NUMA 0 is the target)", [(p, NIC[p], NIC[p], 0) for p in (1, 2, 3)])
    together("ports 0,1 matched", [(p, NIC[p], NIC[p], 0) for p in (0, 1)])
