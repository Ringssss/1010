"""Cross-node weight restore over IB, structured exactly like the probe that works here.

Why this file is a copy of probe ordering rather than a "cleaner" design: on this RCCL build a
1 GiB payload allocated before init_process_group makes the first pair broadcast hang, while the
same broadcast works when the payload is allocated after the warm collective. That is a driver
behaviour, not a preference, so the ordering below is deliberate and should not be tidied away.

Measured on node27/node28 (2026-11-10): 1 GiB, 256 MiB chunks, 44-47 GB/s, both directions.

Completion is the receiver's ack, not the sender's collective: a 1 GiB send "finished" in 0.001 s
because broadcast is issued asynchronously, and the source then exited and tore down the store
under the receiver.
"""
import argparse
import datetime
import hashlib
import json
import socket
import time

import torch
import torch.distributed as dist

MiB = 1 << 20


def stamp(**kw):
    kw["t"] = round(time.time() % 100000, 3)
    print(json.dumps(kw), flush=True)


def ack_listener(port):
    """Bind before any collective so the receiver's ack can never hit a closed port."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(1)
    return srv


def wait_ack(srv, timeout=1800):
    srv.settimeout(timeout)
    t0 = time.perf_counter()
    conn, _ = srv.accept()
    with conn:
        conn.recv(4096)
    srv.close()
    return time.perf_counter() - t0


def send_ack(host, port, msg=b"done"):
    for _ in range(120):
        try:
            with socket.create_connection((host, port), timeout=5) as c:
                c.sendall(msg)
            return True
        except OSError:
            time.sleep(0.5)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", choices=["source", "dest"], required=True)
    ap.add_argument("--master", required=True)
    ap.add_argument("--gib", type=float, default=1.0)
    ap.add_argument("--chunk-mib", type=int, default=256)
    ap.add_argument("--host-staged", action="store_true",
                    help="source keeps the payload in pinned DRAM and stages each chunk to HBM")
    ap.add_argument("--ack-port", type=int, default=40500)
    ap.add_argument("--peer-ip", default="12.11.3.28")
    a = ap.parse_args()

    rank = 0 if a.role == "source" else 1
    n = int(a.gib * 2**30)
    chunk = a.chunk_mib * MiB
    torch.cuda.set_device(0)
    dev = torch.device("cuda", 0)

    ack_srv = ack_listener(a.ack_port) if a.role == "source" else None

    dist.init_process_group("nccl", init_method=f"tcp://{a.master}", rank=rank, world_size=2,
                            timeout=datetime.timedelta(seconds=180))
    g = dist.new_group([0, 1])
    warm = torch.zeros(1, device=dev)
    dist.broadcast(warm, src=0, group=g)          # communicator created before anything is timed
    stamp(ev="warm", role=a.role)

    # Payload buffers AFTER the warm collective, matching the probe's ordering (see module doc).
    staging = torch.empty(chunk, dtype=torch.uint8, device=dev)
    if a.role == "source":
        payload = (torch.empty(n, dtype=torch.uint8, pin_memory=True) if a.host_staged
                   else torch.empty(n, dtype=torch.uint8, device=dev))
        payload.fill_(3)
    else:
        payload = torch.empty(n, dtype=torch.uint8, device=dev)
    # NOTE: no torch.cuda.synchronize() on the source from here on. On this RCCL build a host-side
    # device sync after a pending cross-node collective never returns; every hung run had one, and
    # the only runs that completed 44-47 GB/s avoided it. Ordering is preserved by the stream, and
    # completion is established by the receiver's ack.
    dist.barrier()
    stamp(ev="ready", role=a.role)

    if a.role == "source":
        t0 = time.perf_counter()
        for off in range(0, n, chunk):
            m = min(chunk, n - off)
            part = staging[:m] if a.host_staged else payload[off:off + m]
            if a.host_staged:
                part.copy_(payload[off:off + m], non_blocking=False)   # pinned DRAM -> HBM staging
            dist.broadcast(part, src=0, group=g)
        issue = time.perf_counter() - t0
        ack = wait_ack(ack_srv)                   # hold the process open until the bytes landed
        total = issue + ack
        stamp(ev="sent", GiB=round(n / 2**30, 3), issue_s=round(issue, 4), ack_s=round(ack, 4),
              total_s=round(total, 4), GBps=round(n / total / 1e9, 2), staged=a.host_staged)
    else:
        t0 = time.perf_counter()
        for off in range(0, n, chunk):
            m = min(chunk, n - off)
            dist.broadcast(payload[off:off + m], src=0, group=g)
        torch.cuda.synchronize()
        dt = time.perf_counter() - t0
        head = hashlib.sha256(bytes(payload[:min(n, 64 << 20)].cpu().numpy())).hexdigest()[:16]
        acked = send_ack(a.peer_ip, a.ack_port)
        stamp(ev="received", GiB=round(n / 2**30, 3), s=round(dt, 4), GBps=round(n / dt / 1e9, 2),
              sha_head64MiB=head, acked=acked,
              hbm_alloc_GiB=round(torch.cuda.memory_allocated(dev) / 2**30, 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
