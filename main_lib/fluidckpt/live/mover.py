"""Chunk mover: the one primitive FluidCKPT needs to bring weights back fast.

A source worker holds a model's bytes in pinned DRAM. Each 256 MiB chunk is broadcast on a
two-rank pair group to the destination worker, which scatters it into the live engine's HBM.

Why the code looks like this (measured on this fabric, 2026-11-10):
  * A pair broadcast reaches 45-47 GB/s -- the same order as a local H2D copy.
  * But on the *sender*, waiting for a host-side sync after several chunked collectives never
    returns (the receiver finishes; the sender blocks forever). One chunk is fine, four are not.
    So completion is acknowledged by the receiver over a plain socket, and the sender never waits
    on its own collective. This is the whole reason for the ack channel below.

Deliberately small: one class, no scheduler, no planner coupling. The planner decides *where*
bytes come from; this decides *how* they move.

NCCL has no CPU backend, so every chunk crosses a GPU staging buffer on both ends: pinned DRAM ->
staging (H2D) -> broadcast -> staging -> destination. That double hop is the price of using the
IB fabric at 45+ GB/s instead of TCP.
"""
from __future__ import annotations

import json
import socket
import threading
import time

import torch
import torch.distributed as dist

MiB = 1 << 20
DEFAULT_CHUNK = 256 * MiB


class Mover:
    """Keeps pinned chunks and streams them to peers over pre-created pair groups."""

    def __init__(self, rank, members, pair_of, ack_base, dev, chunk_bytes=DEFAULT_CHUNK,
                 staging=None):
        self.rank, self.members, self.pair_of = rank, members, pair_of
        self.dev = dev
        self.chunk = chunk_bytes
        self.buffers = {}                 # chunk_id -> pinned tensor (this worker's copies)
        # Staging must exist before the first collective: a fresh cudaMalloc while a cross-node
        # collective is pending makes the driver synchronise the device and never return, which
        # is exactly how the earlier two-rank harness hung.
        self.staging = staging if staging is not None else torch.empty(
            chunk_bytes, dtype=torch.uint8, device=dev)
        self.lock = threading.Lock()
        self.stats = {"sent_bytes": 0, "recv_bytes": 0, "chunks": 0}
        self.ack_base = ack_base
        self._ack = _Ack(ack_base + rank)

    # ---- storage -----------------------------------------------------------------------
    def hold(self, chunk_id, nbytes):
        """Allocate a pinned copy of a chunk on this worker."""
        with self.lock:
            if chunk_id in self.buffers:
                return
            self.buffers[chunk_id] = torch.empty(nbytes, dtype=torch.uint8, pin_memory=True)

    def buffer(self, chunk_id):
        return self.buffers[chunk_id]

    def drop(self, chunk_id):
        with self.lock:
            self.buffers.pop(chunk_id, None)

    # ---- transfer ----------------------------------------------------------------------
    def send(self, dst_w, chunk_id):
        """Broadcast one chunk to a peer.

        Two rules, both measured on this fabric rather than assumed:

        * NCCL has no CPU backend, so the pinned copy is staged into HBM first. The staging copy
          is synchronous (one cuda sync before the collectives), never per block inside the loop --
          interleaving a host sync with a chain of collectives is what deadlocked the earlier
          executor.
        * The sender issues every broadcast and then waits for the receiver's ack. It does not
          wait on its own collective completion: after several chunked broadcasts that host-side
          wait never returns (receiver finishes, sender blocks), while the bytes have in fact
          arrived. The receiver is the authority, so the ack is the completion signal.
        """
        buf = self.buffers[chunk_id]
        g = self.pair_of[(self.rank, dst_w)]
        t0 = time.time()
        n = buf.numel()
        staging = self.staging[:n] if n <= self.chunk else None
        if staging is not None:
            staging.copy_(buf, non_blocking=False)             # one copy, one sync
            torch.cuda.synchronize(self.dev)
            dist.broadcast(staging, src=self.rank, group=g)
        else:
            for off in range(0, n, self.chunk):
                m = min(self.chunk, n - off)
                part = self.staging[:m]
                part.copy_(buf[off:off + m], non_blocking=False)
                torch.cuda.synchronize(self.dev)
                dist.broadcast(part, src=self.rank, group=g)
        ack_s = self._ack.wait(dst_w, chunk_id, timeout=300)
        self.stats["sent_bytes"] += n
        self.stats["chunks"] += 1
        return {"chunk": chunk_id[:16], "bytes": n, "issue_s": round(time.time() - t0 - ack_s, 4),
                "ack_s": round(ack_s, 4), "GBps": round(n / max(time.time() - t0, 1e-9) / 1e9, 2)}

    def recv(self, src_w, chunk_id, sink):
        """Receive one chunk and hand each block to `sink.put(off, gpu_part)`; then acknowledge.

        The sink decides where bytes land: `shard_into` keeps them in HBM, `host_into` copies
        them back to pinned DRAM. Keeping this open is what lets the same primitive serve both
        'restore into a running engine' and 'park a copy in DRAM'.
        """
        g = self.pair_of[(self.rank, src_w)]
        n = sink.nbytes
        t0 = time.time()
        for off in range(0, n, self.chunk):
            m = min(self.chunk, n - off)
            part = self.staging[:m]
            dist.broadcast(part, src=src_w, group=g)
            sink.put(off, part)
        self._ack.signal(self.rank, src_w, chunk_id)
        self.stats["recv_bytes"] += n
        self.stats["chunks"] += 1
        return {"chunk": chunk_id[:16], "bytes": n, "total_s": round(time.time() - t0, 4),
                "GBps": round(n / max(time.time() - t0, 1e-9) / 1e9, 2)}


class HostSink:
    """Land arriving chunks in pinned DRAM (parking a copy for later)."""

    def __init__(self, nbytes):
        self.nbytes = nbytes
        self.buf = torch.empty(nbytes, dtype=torch.uint8, pin_memory=True)

    def put(self, off, part):
        m = part.numel()
        self.buf[off:off + m].copy_(part, non_blocking=False)


class HbmSink:
    """Land arriving chunks straight into a target HBM buffer (restore into a running engine)."""

    def __init__(self, hbm):
        self.nbytes = hbm.numel()
        self.hbm = hbm

    def put(self, off, part):
        m = part.numel()
        self.hbm[off:off + m].copy_(part, non_blocking=False)


class _Ack:
    """Tiny receiver-confirms-sender channel, keyed (src_rank, dst_rank, chunk_id).

    Broadcast completion on the sender is unreliable after the first couple of collectives, so the
    authority on "the bytes arrived" is the receiver. Both sides speak this over TCP.
    """

    def __init__(self, port):
        self.port = port
        self.base = port               # rank r listens on base + r
        self.pending = {}
        self.cond = threading.Condition()
        self.ready = {}
        self.srv = None
        self._start_server()

    def _start_server(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.port))
        srv.listen(64)
        self.srv = srv

        def loop():
            while True:
                try:
                    c, _ = srv.accept()
                except OSError:
                    return
                threading.Thread(target=self._handle, args=(c,), daemon=True).start()

        threading.Thread(target=loop, daemon=True).start()

    def _handle(self, conn):
        with conn:
            data = b""
            while not data.endswith(b"\n"):
                part = conn.recv(4096)
                if not part:
                    return
                data += part
            msg = json.loads(data)
            with self.cond:
                self.pending.setdefault((msg["to"], msg["chunk"]), msg["t"])
                self.cond.notify_all()

    def signal(self, receiver_rank, sender_rank, chunk_id):
        """Receiver -> sender: connect to the SENDER's ack server and post the confirmation."""
        host = self.ready.get(sender_rank)
        if host is None:
            raise RuntimeError(f"no ack route to rank {sender_rank}")
        with socket.create_connection((host, self.port), timeout=5) as c:
            c.sendall(json.dumps({"to": receiver_rank, "chunk": chunk_id, "t": time.time()}).encode() + b"\n")

    def wait(self, from_rank, chunk_id, timeout=300):
        t0 = time.time()
        with self.cond:
            while (from_rank, chunk_id) not in self.pending:
                left = timeout - (time.time() - t0)
                if left <= 0:
                    raise TimeoutError(f"no ack from rank {from_rank} for chunk {chunk_id[:12]}")
                self.cond.wait(min(left, 1.0))
            return time.time() - self.pending.pop((from_rank, chunk_id))
