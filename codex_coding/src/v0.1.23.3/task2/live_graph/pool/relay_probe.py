"""xGMI relay go/no-go: can one card take in more than its own PCIe link (~47 GB/s) when the
other HCA ports land data on neighbour cards and the bytes are forwarded over xGMI?

  relay_probe.py <src.json>[,<src.json>...] <out.log> [--target 1] [--relays 0:0,2:4,3:7]
                 [--h2d-relays 3,6] [--secs 8] [--warm 60]

Run inside zjn-fluid-pool-<nn> (HSA_FORCE_FINE_GRAIN_PCIE=1) while affinity_probe.py src is
alive on the source nodes. Port p of a source is read by port p here (four subnets).

Cases (each measured over --secs after a short ramp; bytes counted only inside the window):
  direct        target's near port writes straight into the target card (the wall today)
  xgmi_pair     D2D relay card -> target, one relay, no network
  xgmi_fanin    D2D from every relay card at once -> target
  relay         ports -> slot ring on their near relay card -> D2D into target (no direct)
  full          direct + relay at once                                  <- the gate (>= 120 GB/s)
  full_h2d      full + local pinned DRAM -> H2D into extra relay cards -> D2D into target
  tenant_*      an HBM-bound copy loop on one relay card, alone and while `relay` runs
"""
import argparse
import json
import queue
import threading
import time

import torch

from fluidckpt.live import rdma
from fluidckpt.live.noded import prefer_numa

M, G = 1 << 20, 1 << 30
NIC_NUMA = [0, 1, 4, 5]                 # HCA port p sits on NUMA node NIC_NUMA[p]
NEAR = {0: (0, 2), 1: (3, 1), 2: (4, 6), 3: (7, 5)}   # port -> its two near cards (capacity table)

ap = argparse.ArgumentParser()
ap.add_argument("srcs")
ap.add_argument("out")
ap.add_argument("--target", type=int, default=1)
ap.add_argument("--relays", default="0:0,2:4,3:7", help="port:card, one relay card per borrowed port")
ap.add_argument("--h2d-relays", default="3,6")
ap.add_argument("--secs", type=float, default=8.0)
ap.add_argument("--ramp", type=float, default=2.0)
ap.add_argument("--warm", type=float, default=60.0)
ap.add_argument("--slot-mib", type=int, default=64)
ap.add_argument("--slots", type=int, default=8)
ap.add_argument("--conns", type=int, default=2, help="RDMA connections (threads) per port")
ap.add_argument("--copy", choices=["push", "pull"], default="push",
                help="push: torch copy_ (runs on the relay card's stream); pull: hipMemcpyAsync on a stream "
                     "of the target card, so the relay card runs no kernel")
ap.add_argument("--cases", default="all", help="comma list of cases to run (default: all)")
a = ap.parse_args()

out = open(a.out, "a")


def say(**k):
    k.setdefault("t", round(time.time(), 3))
    line = json.dumps(k)
    out.write(line + "\n")
    out.flush()
    print(line, flush=True)


srcs = [json.load(open(p)) for p in a.srcs.split(",")]
me = None
for ip in ("12.11.3.27", "12.11.3.28", "12.11.3.29", "12.11.3.30"):
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((ip, 0))
        s.close()
        me = ip
        break
    except OSError:
        continue
assert me, "cannot find this node's ib0 address"
T = a.target
tport = next(p for p, cs in NEAR.items() if T in cs)
relays = {int(p): int(c) for p, c in (x.split(":") for x in a.relays.split(","))}
assert tport not in relays and T not in relays.values()
h2d_relays = [int(c) for c in a.h2d_relays.split(",") if c != ""]
S = a.slot_mib * M
say(event="config", copy=a.copy, me=me, target=T, target_port=tport, relays=relays, h2d_relays=h2d_relays,
    srcs=[s["ip"] for s in srcs], slot_MiB=a.slot_mib, slots=a.slots, conns=a.conns, secs=a.secs)

# ---- buffers: long-lived and registered once (fresh device memory is slow for direct writes)
LAND = 4 * G
dst = torch.empty(LAND, dtype=torch.uint8, device=f"cuda:{T}")
dst_reg = rdma.Region(tport, dst.data_ptr(), LAND, False)
ring = {}
for p, c in relays.items():
    t = torch.empty(a.slots * S, dtype=torch.uint8, device=f"cuda:{c}")
    ring[c] = {"t": t, "reg": rdma.Region(p, t.data_ptr(), a.slots * S, False), "port": p}
for c in h2d_relays:
    ring[c] = {"t": torch.empty(a.slots * S, dtype=torch.uint8, device=f"cuda:{c}"), "reg": None, "port": None}
host = {}
for c in h2d_relays:                     # pinned host memory near the relay card
    prefer_numa(c)
    host[c] = torch.empty(2 * G, dtype=torch.uint8, pin_memory=True)
    prefer_numa(None)
torch.cuda.synchronize()


def src_region(p, k):
    """Source for port p, stream k: spread ports over the source nodes, NUMA-matched region."""
    s = srcs[p % len(srcs)]
    return s, s["regions"][str(NIC_NUMA[p])]


conns = {}


def conn(p, k):
    key = (p, k)
    if key not in conns:
        s, _ = src_region(p, k)
        conns[key] = rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(s["ip"], p), s["port"])
    return conns[key]


for p in [tport] + list(relays):
    for k in range(a.conns):
        conn(p, k)

import ctypes
_hip = ctypes.CDLL("libamdhip64.so")
_hip.hipMemcpyAsync.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_void_p]


def d2d(dst_t, src_t, s_src, s_dst):
    """Forward src_t (relay card) into dst_t (target): push on the relay's stream, or pull on the target's."""
    if a.copy == "push":
        with torch.cuda.stream(s_src), torch.cuda.stream(s_dst):
            dst_t.copy_(src_t, non_blocking=True)
        s_src.synchronize()
        s_dst.synchronize()
    else:
        with torch.cuda.device(T):
            rc = _hip.hipMemcpyAsync(ctypes.c_void_p(dst_t.data_ptr()), ctypes.c_void_p(src_t.data_ptr()),
                                     src_t.numel(), 3, ctypes.c_void_p(s_dst.cuda_stream))
            assert rc == 0, f"hipMemcpyAsync {rc}"
            s_dst.synchronize()


# ---- workers; every worker adds delivered bytes to cnt[name]
cnt = {}
lock = threading.Lock()


def add(name, n):
    with lock:
        cnt[name] = cnt.get(name, 0) + n


def w_direct(stop, k):
    _, r = src_region(tport, k)
    half = LAND // 2
    while not stop.is_set():
        conn(tport, k).read(r["addr"], r["rkeys"][tport], dst.data_ptr() + k * (half // a.conns), dst_reg.lkey,
                            half // a.conns, 4 * M, 8)
        add("direct", half // a.conns)


def relay_pipe(stop, c, fill_threads, label):
    """One relay card: fill threads put bytes into free slots, the copier forwards them to T."""
    free, full = queue.Queue(), queue.Queue()
    for i in range(a.slots):
        free.put(i)
    rt = ring[c]["t"]
    s_src = torch.cuda.Stream(device=f"cuda:{c}")
    s_dst = torch.cuda.Stream(device=f"cuda:{T}")

    def copier():
        off = LAND // 2
        while not stop.is_set():
            try:
                i = full.get(timeout=0.2)
            except queue.Empty:
                continue
            d2d(dst[off:off + S], rt[i * S:(i + 1) * S], s_src, s_dst)
            free.put(i)
            add(label, S)
            off = LAND // 2 + ((off - LAND // 2 + S) % (LAND // 2 - S))

    th = [threading.Thread(target=copier, daemon=True)]
    for f in fill_threads:
        th.append(threading.Thread(target=f, args=(stop, free, full), daemon=True))
    return th


def rdma_filler(p, c, k):
    _, r = src_region(p, k)
    reg = ring[c]["reg"]
    base = ring[c]["t"].data_ptr()

    def f(stop, free, full):
        while not stop.is_set():
            try:
                i = free.get(timeout=0.2)
            except queue.Empty:
                continue
            conn(p, k).read(r["addr"] + (i * S) % (2 * G - S), r["rkeys"][p], base + i * S, reg.lkey, S, 4 * M, 8)
            full.put(i)
    return f


def h2d_filler(c):
    rt, h = ring[c]["t"], host[c]
    st = torch.cuda.Stream(device=f"cuda:{c}")

    def f(stop, free, full):
        while not stop.is_set():
            try:
                i = free.get(timeout=0.2)
            except queue.Empty:
                continue
            with torch.cuda.stream(st):
                rt[i * S:(i + 1) * S].copy_(h[(i * S) % (2 * G - S):(i * S) % (2 * G - S) + S], non_blocking=True)
            st.synchronize()
            full.put(i)
    return f


def xgmi_loop(stop, c, label):
    rt = ring[c]["t"]
    s_src = torch.cuda.Stream(device=f"cuda:{c}")
    s_dst = torch.cuda.Stream(device=f"cuda:{T}")
    off = 0
    n = a.slots * S
    while not stop.is_set():
        d2d(dst[off:off + n], rt, s_src, s_dst)
        add(label, n)
        off = (off + n) % (LAND - n)


def tenant_loop(stop, c, label):
    """HBM-bound tenant: copy 2 GiB inside card c, again and again (read+write = 4 GiB moved)."""
    x = torch.empty(2 * G, dtype=torch.uint8, device=f"cuda:{c}")
    y = torch.empty(2 * G, dtype=torch.uint8, device=f"cuda:{c}")
    st = torch.cuda.Stream(device=f"cuda:{c}")
    while not stop.is_set():
        with torch.cuda.stream(st):
            y.copy_(x, non_blocking=True)
        st.synchronize()
        add(label, 4 * G)


CASES = None if a.cases == "all" else set(a.cases.split(","))


def measure(case, parts, secs=None, quiet=False):
    """parts: list of builder(stop) -> [threads]. Counts bytes in a window after a ramp."""
    if CASES is not None and case not in CASES and case != "warm":
        return {}, 0.0
    secs = a.secs if secs is None else secs
    stop = threading.Event()
    th = []
    for b in parts:
        th += b(stop)
    cnt.clear()
    for x in th:
        x.start()
    time.sleep(a.ramp)
    with lock:
        c0 = dict(cnt)
    t0 = time.time()
    time.sleep(secs)
    with lock:
        c1 = dict(cnt)
    dt = time.time() - t0
    stop.set()
    for x in th:
        x.join(timeout=30)
    rates = {k: round((c1.get(k, 0) - c0.get(k, 0)) / dt / 1e9, 1) for k in c1}
    into_target = round(sum(v for k, v in rates.items() if not k.startswith("tenant")), 1)
    if not quiet:
        say(event="case", case=case, GBps=rates, into_target_GBps=into_target, window_s=round(dt, 2))
    return rates, into_target


def B_direct(stop):
    return [threading.Thread(target=w_direct, args=(stop, k), daemon=True) for k in range(a.conns)]


def B_relay(stop):
    th = []
    for p, c in relays.items():
        th += relay_pipe(stop, c, [rdma_filler(p, c, k) for k in range(a.conns)], f"relay:p{p}:c{c}")
    return th


def B_h2d_relay(stop):
    th = []
    for c in h2d_relays:
        th += relay_pipe(stop, c, [h2d_filler(c)], f"h2d_relay:c{c}")
    return th


def B_xgmi(cards):
    def b(stop):
        return [threading.Thread(target=xgmi_loop, args=(stop, c, f"xgmi:c{c}"), daemon=True) for c in cards]
    return b


def B_tenant(c):
    def b(stop):
        return [threading.Thread(target=tenant_loop, args=(stop, c, f"tenant:c{c}"), daemon=True)]
    return b


# ---- warm the registered device memory (direct writes into fresh memory under-run for a while)
if a.warm > 0:
    say(event="warm_start", seconds=a.warm)
    r, tot = measure("warm", [B_direct, B_relay], secs=a.warm)
first_relay = next(iter(relays.values()))
measure("direct", [B_direct])
measure("xgmi_pair", [B_xgmi([first_relay])])
measure("xgmi_fanin", [B_xgmi(list(relays.values()) + h2d_relays)])
measure("relay", [B_relay])
measure("full", [B_direct, B_relay])
if h2d_relays:
    measure("full_h2d", [B_direct, B_relay, B_h2d_relay])
measure("full_again", [B_direct, B_relay])
tc = list(relays.values())[1] if len(relays) > 1 else first_relay
def B_nic_only(c):
    """NIC writes into card c's ring, nothing forwarded (isolates PCIe-in traffic on the relay)."""
    p = next(q for q, x in relays.items() if x == c)

    def b(stop):
        def f(k):
            _, r = src_region(p, k)
            base, reg = ring[c]["t"].data_ptr(), ring[c]["reg"]
            i = k
            while not stop.is_set():
                conn(p, k).read(r["addr"], r["rkeys"][p], base + (i % a.slots) * S, reg.lkey, S, 4 * M, 8)
                add(f"nic_only:c{c}", S)
                i += a.conns
        return [threading.Thread(target=f, args=(k,), daemon=True) for k in range(a.conns)]
    return b


alone, _ = measure("tenant_alone", [B_tenant(tc)])
for case, parts in (("tenant_with_nic_only", [B_tenant(tc), B_nic_only(tc)]),
                    ("tenant_with_xgmi_only", [B_tenant(tc), B_xgmi([tc])])):
    r, _ = measure(case, parts)
    if r:
        say(event="tenant_slowdown_part", copy=a.copy, case=case, card=tc,
            slowdown_pct=round(100 * (1 - r.get(f"tenant:c{tc}", 0) / alone[f"tenant:c{tc}"]), 1))
both, _ = measure("tenant_with_relay", [B_tenant(tc), B_relay])
ta, tb = alone.get(f"tenant:c{tc}", 0), both.get(f"tenant:c{tc}", 0)
say(event="tenant_slowdown", copy=a.copy, card=tc, alone_GBps=ta, with_relay_GBps=tb,
    slowdown_pct=round(100 * (1 - tb / ta), 1) if ta else None)
say(event="done")
