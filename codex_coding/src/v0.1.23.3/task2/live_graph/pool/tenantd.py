"""Another tenant, on demand: real occupancy and real traffic on one node's media and links.

Runs next to the node daemon (same container) and does what other users of the cluster do, so the
live graph sees it through the ordinary probes and nothing in FluidPool is told about it:

  dram  numa, gib      hold `gib` GiB of locked anonymous memory on a NUMA node (0 releases)
  hbm   card, gib      hold `gib` GiB of device memory on a card (0 releases)
  ssd   mode, seconds  O_DIRECT sequential write or read traffic on the node's NVMe pool
  ib    peer, ports, seconds   RDMA READ traffic from a peer's tenant region into this node
  h2d   card, seconds  continuous H2D copies into a card (PCIe traffic)
  status / clear

RPC: same framing as noded (length-prefixed JSON), port 18770.
"""
import ctypes
import json
import mmap
import os
import socket
import sys
import threading
import time

sys.path.insert(0, "/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib")
from fluidckpt.live import rdma                                          # noqa: E402
from fluidckpt.live.noded import prefer_numa, recv_msg, send_msg         # noqa: E402

GiB, MiB = 1 << 30, 1 << 20
PORT, RPORT = 18770, 18771
libc = ctypes.CDLL("libc.so.6", use_errno=True)


class Tenant:
    def __init__(self, ip, ssd_dir, cards):
        self.ip, self.ssd_dir, self.cards = ip, ssd_dir, cards
        self.dram = {}                  # numa -> [mmap blocks of 1 GiB]
        self.hbm = {}                   # card -> tensor
        self.jobs = {}                  # name -> (thread, stop event, stats)
        self.lock = threading.Lock()
        self.torch = None
        # 2 GiB region other tenants can pull from (the source side of IB traffic)
        self.src = mmap.mmap(-1, 2 * GiB)
        self.src_addr = ctypes.addressof(ctypes.c_char.from_buffer(self.src))
        self.src_regs = [rdma.Region(p, self.src_addr, 2 * GiB, True) for p in range(rdma.PORTS)]
        self.dst = mmap.mmap(-1, 4 * GiB)
        self.dst_addr = ctypes.addressof(ctypes.c_char.from_buffer(self.dst))
        self.dst_regs = [rdma.Region(p, self.dst_addr, 4 * GiB, False) for p in range(rdma.PORTS)]
        for p in range(rdma.PORTS):
            rdma.serve(rdma.port_ip(ip, p), RPORT)

    def _torch(self):
        if self.torch is None:
            import torch
            self.torch = torch
        return self.torch

    # ---- occupancy -----------------------------------------------------------------------
    def op_dram(self, numa, gib):
        """Grow or shrink the locked memory held on `numa` to `gib` GiB. Returns when done."""
        t0 = time.time()
        blocks = self.dram.setdefault(int(numa), [])
        want = int(gib)
        while len(blocks) > want:
            b = blocks.pop()
            b.close()
        need = want - len(blocks)
        new, errs = [], []

        def grab():
            prefer_numa(numa, strict=True)
            try:
                m = mmap.mmap(-1, GiB)
                addr = ctypes.addressof(ctypes.c_char.from_buffer(m))
                ctypes.memset(addr, 1, GiB)             # touch: this is what takes the memory
                if libc.mlock(ctypes.c_void_p(addr), ctypes.c_size_t(GiB)) != 0:
                    errs.append(os.strerror(ctypes.get_errno()))
                new.append(m)
            finally:
                prefer_numa(None)
        th = [threading.Thread(target=grab) for _ in range(need)]
        [x.start() for x in th]
        [x.join() for x in th]
        blocks += new
        return {"numa": numa, "held_GiB": len(blocks), "s": round(time.time() - t0, 3), "t_done": time.time(),
                "t_start": t0, "mlock_errors": errs[:1]}

    def op_hbm(self, card, gib):
        torch = self._torch()
        t0 = time.time()
        self.hbm.pop(int(card), None)
        torch.cuda.empty_cache()
        if gib > 0:
            t = torch.empty(int(gib * GiB), dtype=torch.uint8, device=f"cuda:{int(card)}")
            t.fill_(1)
            torch.cuda.synchronize(f"cuda:{int(card)}")
            self.hbm[int(card)] = t
        return {"card": card, "held_GiB": gib, "s": round(time.time() - t0, 3), "t_start": t0, "t_done": time.time()}

    # ---- traffic -------------------------------------------------------------------------
    def _job(self, name, fn, seconds):
        stop, stats = threading.Event(), {"bytes": 0, "t_start": time.time()}

        def run():
            try:
                fn(stop, stats, time.time() + seconds)
            except Exception as e:                      # noqa: BLE001
                stats["error"] = repr(e)
            stats["t_end"] = time.time()
        th = threading.Thread(target=run, daemon=True)
        with self.lock:
            self.jobs[name] = (th, stop, stats)
        th.start()
        return {"job": name, "t_start": stats["t_start"]}

    def op_ssd(self, mode, seconds, streams=4):
        os.makedirs(self.ssd_dir, exist_ok=True)
        buf = mmap.mmap(-1, 64 * MiB)
        buf.write(os.urandom(MiB) * 64)

        def one(k, stop, stats, until):
            path = os.path.join(self.ssd_dir, f"tenant_{k}.bin")
            if mode == "read" and not (os.path.exists(path) and os.path.getsize(path) >= 4 * GiB):
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_DIRECT, 0o644)
                for _ in range(64):
                    os.write(fd, buf)
                os.close(fd)
            flags = os.O_DIRECT | (os.O_RDONLY if mode == "read" else os.O_WRONLY | os.O_CREAT)
            fd = os.open(path, flags, 0o644)
            off = 0
            while not stop.is_set() and time.time() < until:
                if mode == "read":
                    n = os.preadv(fd, [buf], off)
                else:
                    n = os.pwrite(fd, buf, off)
                stats["bytes"] += n
                off = (off + 64 * MiB) % (4 * GiB)
            os.close(fd)

        def fn(stop, stats, until):
            th = [threading.Thread(target=one, args=(k, stop, stats, until)) for k in range(streams)]
            [x.start() for x in th]
            [x.join() for x in th]
        return self._job(f"ssd_{mode}", fn, seconds)

    def op_ib(self, peer, ports, seconds):
        """Pull from `peer`'s tenant region on the given HCA ports (traffic INTO this node)."""
        cl = socket.create_connection((peer, PORT), timeout=10)
        send_msg(cl, {"op": "region"})
        info = recv_msg(cl)["ok"]
        cl.close()

        def fn(stop, stats, until):
            def one(p):
                c = rdma.Conn(rdma.port_ip(self.ip, p), rdma.port_ip(peer, p), RPORT)
                while not stop.is_set() and time.time() < until:
                    c.read(info["addr"], info["rkeys"][p], self.dst_addr + p * GiB, self.dst_regs[p].lkey,
                           GiB, 4 * MiB, 8)
                    stats["bytes"] += GiB
                c.close()
            th = [threading.Thread(target=one, args=(p,)) for p in ports]
            [x.start() for x in th]
            [x.join() for x in th]
        return self._job("ib_" + "".join(map(str, ports)), fn, seconds)

    def op_region(self):
        return {"addr": self.src_addr, "rkeys": [r.rkey for r in self.src_regs]}

    def op_h2d(self, card, seconds, gib=2):
        torch = self._torch()
        dev = f"cuda:{int(card)}"
        g = torch.empty(int(gib * GiB), dtype=torch.uint8, device=dev)
        h = torch.empty(int(gib * GiB), dtype=torch.uint8, pin_memory=True)

        def fn(stop, stats, until):
            while not stop.is_set() and time.time() < until:
                g.copy_(h, non_blocking=True)
                torch.cuda.synchronize(dev)
                stats["bytes"] += h.numel()
        return self._job(f"h2d_{card}", fn, seconds)

    def op_status(self):
        with self.lock:
            jobs = {k: {**v[2], "alive": v[0].is_alive()} for k, v in self.jobs.items()}
        return {"dram_GiB": {k: len(v) for k, v in self.dram.items() if v},
                "hbm_GiB": {k: v.numel() / GiB for k, v in self.hbm.items()}, "jobs": jobs, "t": time.time()}

    def op_clear(self):
        with self.lock:
            jobs = list(self.jobs.values())
        for th, stop, _ in jobs:
            stop.set()
        for th, _, _ in jobs:
            th.join(10)
        with self.lock:
            self.jobs.clear()
        for blocks in self.dram.values():
            while blocks:
                blocks.pop().close()
        self.hbm.clear()
        if self.torch is not None:
            self.torch.cuda.empty_cache()
        return True

    def serve(self):
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        for _ in range(60):
            try:
                srv.bind((self.ip, PORT))
                break
            except OSError:
                time.sleep(0.5)
        srv.listen(32)
        print(json.dumps({"event": "tenantd", "ip": self.ip}), flush=True)
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        with conn:
            while True:
                try:
                    msg = recv_msg(conn)
                except (ConnectionError, OSError):
                    return
                op = msg.pop("op")
                try:
                    out = {"ok": getattr(self, "op_" + op)(**msg)}
                except Exception as e:                  # noqa: BLE001
                    out = {"error": repr(e)}
                try:
                    send_msg(conn, out)
                except OSError:
                    return


if __name__ == "__main__":
    ip = sys.argv[1]
    Tenant(ip, sys.argv[2] if len(sys.argv) > 2 else "/ssdpool/tenant", []).serve()
