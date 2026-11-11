"""Restore a parked model from one node into another node's HBM over IB.

This file is deliberately shaped like the probe that reliably reaches 45-47 GB/s on this fabric
(pair_bcast.py): create the process group, create the pair group, one warm broadcast, allocate the
payload buffer, synchronise, barrier, then a loop that contains nothing but broadcasts.

Everything else was tried and hung (2026-11-10, all reproduced):
  * host->device copies inside the broadcast loop,
  * pinned host buffers (cudaHostRegister) anywhere near the collectives,
  * a receiver still allocating memory while a collective was pending,
  * passing the file plan as a collective.

So: the source reads the payload into HBM *before* the loop, the loop moves GPU bytes only, and
completion is the receiver's ack -- never the sender's own host-side sync, which on this driver can
return immediately (1.4 GiB "sent" in 0.001 s) and tear the store down under the receiver.
"""
import argparse
import datetime
import hashlib
import json
import os
import socket
import time

import torch
import torch.distributed as dist

import faulthandler as _fh, signal as _sig
_fh.register(_sig.SIGUSR1)

MiB = 1 << 20


def stamp(**kw):
    kw["t"] = round(time.time() % 100000, 3)
    print(json.dumps(kw), flush=True)


def read_fully(fh, view):
    """Fill `view` completely. A single raw read() may return short (seen at ~2 GiB on this
    filesystem), so a model restore must loop rather than trust one readinto call."""
    total, off = len(view), 0
    while off < total:
        got = fh.readinto(view[off:])
        if not got:
            raise IOError(f"short read at {off}/{total}")
        off += got
    return off


def files_under(root):
    out = []
    for dirpath, _dirs, names in os.walk(root):
        for n in sorted(names):
            if n.endswith((".safetensors", ".bin", ".gguf")):
                p = os.path.join(dirpath, n)
                out.append((os.path.relpath(p, root), p, os.path.getsize(p)))
    return sorted(out)


def ack_listener(port):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(1)
    return srv


def wait_ack(srv, timeout=3600):
    srv.settimeout(timeout)
    t0 = time.perf_counter()
    conn, _ = srv.accept()
    with conn:
        data = conn.recv(65536)
    srv.close()
    return time.perf_counter() - t0, data.decode(errors="replace")


def send_ack(host, port, msg):
    for _ in range(120):
        try:
            with socket.create_connection((host, port), timeout=5) as c:
                c.sendall(json.dumps(msg).encode())
            return True
        except OSError:
            time.sleep(0.5)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", choices=["source", "dest"], required=True)
    ap.add_argument("--master", required=True)
    ap.add_argument("--park-dir", help="both ends read the file plan from here (shared /home)")
    ap.add_argument("--chunk-mib", type=int, default=256)
    ap.add_argument("--ack-port", type=int, default=40600)
    ap.add_argument("--peer-ip", default="12.11.3.28")
    ap.add_argument("--plan-dir", help="shared writable dir for the plan file")
    ap.add_argument("--pinned", action="store_true", help="stage through pinned host memory")
    ap.add_argument("--plan-wait", type=float, default=60.0)
    ap.add_argument("--gpu", type=int, default=0, help="device ordinal inside the container")
    ap.add_argument("--shard-index", type=int, default=0, help="which slice of the file list this channel carries")
    ap.add_argument("--shard-count", type=int, default=1, help="number of parallel channels over the same model")
    a = ap.parse_args()

    rank = 0 if a.role == "source" else 1
    chunk = a.chunk_mib * MiB
    torch.cuda.set_device(a.gpu)
    dev = torch.device("cuda", a.gpu)

    # ---- plan through the shared filesystem ---------------------------------------------------
    # The plan must land somewhere both nodes can write: the park directory is often a
    # read-only mount (/data), so use an explicit shared scratch directory instead.
    plan_dir = a.plan_dir or a.park_dir
    os.makedirs(plan_dir, exist_ok=True)
    meta_path = os.path.join(plan_dir, "restore_plan.json")
    if a.role == "source":
        plan = files_under(a.park_dir)
        with open(meta_path, "w") as fh:
            json.dump(plan, fh)
    else:
        plan, deadline = None, time.time() + a.plan_wait
        while plan is None and time.time() < deadline:
            try:
                with open(meta_path) as fh:
                    plan = json.load(fh)
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(0.3)
        if plan is None:
            raise SystemExit(f"no plan at {meta_path}")
    if a.shard_count > 1:
        # Split the model across channels by file: one TP rank per channel, as a real TP4
        # restore would. Both ends derive the same slice from the same plan file.
        plan = plan[a.shard_index::a.shard_count]
    total_bytes = sum(size for _, _, size in plan)
    stamp(ev="plan", role=a.role, files=len(plan), GiB=round(total_bytes / 2**30, 3))

    # ---- payload buffer BEFORE the process group ----------------------------------------------
    # Measured: a 1.4 GiB block allocated after the warm collective is fine, but a 15.5 GiB block
    # at the same place hangs the receiver forever. A large first-touch allocation synchronises
    # the device, and doing that with the communicator already live on this driver deadlocks.
    # Allocating first costs nothing -- nothing is transferred until every rank is ready anyway.
    payload = torch.empty(total_bytes, dtype=torch.uint8, device=dev)
    torch.cuda.synchronize()
    stamp(ev="allocated", role=a.role, GiB=round(total_bytes / 2**30, 3))

    # ---- process group, exactly as the working probe ------------------------------------------
    dist.init_process_group("nccl", init_method=f"tcp://{a.master}", rank=rank, world_size=2,
                            timeout=datetime.timedelta(seconds=180))
    pg = dist.new_group([0, 1])
    dist.broadcast(torch.zeros(1, device=dev), src=0, group=pg)
    stamp(ev="warm", role=a.role)
    if a.role == "source":
        # Load the whole model into HBM here, so the broadcast loop moves GPU bytes only.
        # Pageable host memory caps the H2D copy at ~2-3 GB/s on this platform, which is exactly
        # what a 19 GiB restore measured (2.05 GB/s) even with a warm page cache. Pinned memory
        # lifts that to line rate; the earlier 'pinned deadlock' was a misdiagnosis of the GPU
        # ordinal bug, so --pinned is the fast path and pageable is only the fallback.
        host = (torch.empty(total_bytes, dtype=torch.uint8, pin_memory=True) if a.pinned
                else torch.empty(total_bytes, dtype=torch.uint8))
        base = 0
        for rel, path, size in plan:
            with open(path, "rb", buffering=0) as fh:
                read_fully(fh, memoryview(host.numpy())[base:base + size])
            base += size
        payload.copy_(host)
        stamp(ev="loaded", GiB=round(total_bytes / 2**30, 3))
    # No host sync and no barrier here. The copy runs on the same stream as the broadcasts that
    # follow, so ordering is already guaranteed, and the broadcasts themselves rendezvous both
    # ranks. An explicit cuda.synchronize() at this point hangs once the payload is large
    # (15 GiB hangs, 1.4 GiB does not) -- the same host-sync-with-live-communicator deadlock seen
    # everywhere else on this driver.
    ack_srv = ack_listener(a.ack_port) if a.role == "source" else None
    stamp(ev="ready", role=a.role)

    # ---- the transfer --------------------------------------------------------------------------
    # Double-buffered: chunk k+1's H2D runs on the copy stream while chunk k's broadcast occupies
    # the comm stream, so a restore is limited by the slower of the two (46-48 GB/s apiece) rather
    # than by their sum. Measured single-stage: H2D 21 ms + broadcast 23 ms per GiB = 19.5 GB/s
    # end-to-end, against 45.4 GB/s for a pure broadcast of the same bytes.
    #
    # Both buffers are fixed and reused, and only GPU-side events order the streams -- no host
    # synchronisation inside the loop and no per-chunk allocation, both of which deadlock this
    # driver once a collective is live.
    bufs = [torch.empty(chunk, dtype=torch.uint8, device=dev) for _ in range(2)]
    copy_s = torch.cuda.Stream(device=dev)
    comm_s = torch.cuda.Stream(device=dev)
    ready = [torch.cuda.Event() for _ in range(2)]        # copy finished for buffer k
    free = [torch.cuda.Event() for _ in range(2)]         # comm finished for buffer k
    for e in free:
        e.record()                                        # buffers start free

    total_chunks = (total_bytes + chunk - 1) // chunk
    t0 = time.perf_counter()
    for i in range(total_chunks):
        off = i * chunk
        n = min(chunk, total_bytes - off)
        k = i & 1
        comm_s.wait_event(free[k])                        # buffer k may be reused
        if a.role == "source":
            with torch.cuda.stream(copy_s):
                bufs[k][:n].copy_(host[off:off + n], non_blocking=True)   # pinned DRAM -> HBM
            ready[k].record(copy_s)
            comm_s.wait_event(ready[k])
            with torch.cuda.stream(comm_s):
                dist.broadcast(bufs[k][:n], src=0, group=pg)
        else:
            with torch.cuda.stream(comm_s):
                dist.broadcast(bufs[k][:n], src=0, group=pg)
            ready[k].record(comm_s)
            copy_s.wait_event(ready[k])
            with torch.cuda.stream(copy_s):
                payload[off:off + n].copy_(bufs[k][:n], non_blocking=True)
    if a.role == "source":
        issue = time.perf_counter() - t0
        ack = wait_ack(ack_srv)
        wall = time.perf_counter() - t0
        stamp(ev="sent", GiB=round(total_bytes / 2**30, 3), issue_s=round(issue, 4),
              ack_s=round(ack[0], 4), wall_s=round(wall, 4),
              GBps=round(total_bytes / wall / 1e9, 2), receiver=ack[1][:400])
    else:
        torch.cuda.synchronize()
        wall = time.perf_counter() - (t0 or time.perf_counter())
        head = hashlib.sha256(bytes(payload[:min(total_bytes, 64 << 20)].cpu().numpy())).hexdigest()[:16]
        ok = send_ack(a.peer_ip, a.ack_port,
                      {"head64MiB": head, "GiB": round(total_bytes / 2**30, 3),
                       "GBps": round(total_bytes / wall / 1e9, 2)})
        stamp(ev="received", GiB=round(total_bytes / 2**30, 3), wall_s=round(wall, 4),
              GBps=round(total_bytes / wall / 1e9, 2), acked=ok, head64MiB=head)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
