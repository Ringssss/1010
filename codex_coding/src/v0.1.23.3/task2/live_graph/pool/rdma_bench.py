"""RDMA READ bench between host memories (no GPU, no torch).
  src: rdma_bench.py src --gib 16 --port 18800 --out info.json      (serves until killed / --seconds)
  dst: rdma_bench.py dst --info info.json --ports 0,1,2,3 --conns 1 [--chunk-mib 64]
Every MiB of the source carries its own index in its first 8 bytes; the puller checks them all."""
import argparse, ctypes, json, mmap, os, socket, struct, sys, threading, time
sys.path.insert(0, "/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib")
from fluidckpt.live import rdma

MiB = 1 << 20
IP = {"m03r2n07": "12.11.3.27", "m03r2n08": "12.11.3.28", "m03r2n09": "12.11.3.29", "m03r2n10": "12.11.3.30"}

def region(nbytes):
    mm = mmap.mmap(-1, nbytes, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
    return mm, ctypes.addressof(ctypes.c_char.from_buffer(mm))

def src(a):
    n = int(a.gib * 1024) * MiB
    t = time.time(); mm, addr = region(n)
    for i in range(n // MiB):
        mm[i * MiB:i * MiB + 8] = struct.pack("<Q", i)
    ctypes.memset(addr, 0, 0)  # no-op; pages are touched by the registration below
    t_alloc = time.time() - t
    me = IP[socket.gethostname()]
    regs, reg_s = {}, {}
    for p in [int(x) for x in a.ports.split(",")]:
        t = time.time(); r = rdma.Region(p, addr, n, remote=True); reg_s[p] = round(time.time() - t, 3)
        regs[p] = r.rkey
        rdma.serve(rdma.port_ip(me, p), a.port)
    json.dump({"ip": me, "port": a.port, "addr": addr, "nbytes": n, "rkeys": regs}, open(a.out + ".tmp", "w"))
    os.replace(a.out + ".tmp", a.out)
    print(json.dumps({"event": "serving", "GiB": a.gib, "alloc_s": round(t_alloc, 3), "reg_s": reg_s,
                      "devices": rdma.devices()}), flush=True)
    time.sleep(a.seconds)

def dst(a):
    info = json.load(open(a.info)); n = info["nbytes"]; chunk = a.chunk_mib * MiB
    me = IP[socket.gethostname()]
    t = time.time(); mm, addr = region(n)
    ctypes.memset(addr, 0, n); t_touch = time.time() - t
    ports = [int(x) for x in a.ports.split(",")]
    lk, reg_s = {}, {}
    for p in ports:
        t = time.time(); r = rdma.Region(p, addr, n, remote=False); reg_s[p] = round(time.time() - t, 3); lk[p] = r.lkey
    conns = [(p, rdma.Conn(rdma.port_ip(me, p), rdma.port_ip(info["ip"], p), info["port"]))
             for p in ports for _ in range(a.conns)]
    offs = list(range(0, n, chunk)); lock = threading.Lock(); per = {}
    def work(p, c):
        done = 0
        while True:
            with lock:
                if not offs: break
                off = offs.pop()
            m = min(chunk, n - off)
            c.read(info["addr"] + off, info["rkeys"][str(p)], addr + off, lk[p], m, a.piece_mib * MiB, a.window)
            done += m
        with lock: per[p] = per.get(p, 0) + done
    for rep in range(a.repeat):
        offs[:] = list(range(0, n, chunk)); per.clear()
        th = [threading.Thread(target=work, args=pc) for pc in conns]
        t0 = time.time(); [x.start() for x in th]; [x.join() for x in th]; dt = time.time() - t0
        bad = sum(1 for i in range(n // MiB) if mm[i * MiB:i * MiB + 8] != struct.pack("<Q", i))
        print(json.dumps({"event": "pull", "rep": rep, "GiB": round(n / 2**30, 2), "ports": ports, "conns": a.conns,
                          "chunk_MiB": a.chunk_mib, "s": round(dt, 4), "GBps": round(n / dt / 1e9, 2),
                          "per_port_GiB": {p: round(v / 2**30, 2) for p, v in sorted(per.items())},
                          "bad_MiB": bad, "touch_s": round(t_touch, 3), "reg_s": reg_s}), flush=True)
        ctypes.memset(addr, 0, n)

p = argparse.ArgumentParser(); p.add_argument("cmd")
p.add_argument("--gib", type=float, default=8); p.add_argument("--port", type=int, default=18800)
p.add_argument("--out"); p.add_argument("--info"); p.add_argument("--ports", default="0,1,2,3")
p.add_argument("--conns", type=int, default=1); p.add_argument("--chunk-mib", type=int, default=64)
p.add_argument("--piece-mib", type=int, default=4); p.add_argument("--window", type=int, default=8)
p.add_argument("--repeat", type=int, default=2); p.add_argument("--seconds", type=float, default=120)
a = p.parse_args(); (src if a.cmd == "src" else dst)(a)
