"""Minimal cross-node broadcast probe: does a 2-rank pair collective work on this fabric at all?

Runs one rank per node inside the live containers, creates one cross-node group, warms it with a
1-byte broadcast, then streams a known number of bytes and reports the rate. Each stage is printed
with a timestamp so a hang is attributable to a specific phase (world init, group creation, first
collective, steady-state stream).

Usage (on node27):
  python3 pair_bcast.py --master 12.11.3.27:39610 --bytes-gib 2 --chunk-mib 256
"""
import argparse
import json
import os
import subprocess
import sys
import time

NODES = {"node27": "12.11.3.27", "node28": "12.11.3.28", "node29": "12.11.3.29", "node30": "12.11.3.30"}
CONTAINER = {n: f"zjn-fluid-live-r01-{n[4:]}" for n in NODES}
REPO = "/home/zhujianian/zhujianian/fluidckpt_zjn"


def stamp(**kw):
    kw["t"] = round(time.time() % 10000, 3)
    print(json.dumps(kw), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="node27:0")
    ap.add_argument("--b", default="node28:0")
    ap.add_argument("--master", default="12.11.3.27:39610")
    ap.add_argument("--bytes-gib", type=float, default=2.0)
    ap.add_argument("--chunk-mib", type=int, default=256)
    ap.add_argument("--skip-sender-sync", action="store_true",
                    help="sender reports issue time only; receiver proves the bytes arrived")
    ap.add_argument("--side-stream", action="store_true",
                    help="issue collectives on a non-default stream, as the keeper executor does")
    a = ap.parse_args()
    (na, ga), (nb, gb) = (a.a.split(":"), a.b.split(":"))
    gpu = {"0": ga, "1": gb}
    tag = f"pair_{os.getpid()}"

    for rank, (node, g) in enumerate([(na, ga), (nb, gb)]):
        code = f"""
import json, time, torch, torch.distributed as dist, os
def st(**kw):
    kw['t'] = round(time.time() % 10000, 3); print(json.dumps(kw), flush=True)
torch.cuda.set_device({g})
dist.init_process_group('nccl', init_method='tcp://{a.master}', rank={rank}, world_size=2,
                        timeout=__import__('datetime').timedelta(seconds=120))
st(ev='world', rank={rank})
g = dist.new_group([0, 1])
st(ev='group', rank={rank})
x = torch.zeros(1, device='cuda')
dist.broadcast(x, src=0, group=g)
st(ev='warm', rank={rank})
n = int({int(a.bytes_gib * 2**30)})
c = int({a.chunk_mib} << 20)
buf = torch.empty(c, dtype=torch.uint8, device='cuda')
side = torch.cuda.Stream() if {str(a.side_stream)} == "True" else None
if {rank} == 0:
    buf.fill_(7)
torch.cuda.synchronize()
dist.barrier()
t0 = time.time()
sent = 0
while sent < n:
    m = min(c, n - sent)
    if side is None:
        dist.broadcast(buf[:m], src=0, group=g)
    else:
        with torch.cuda.stream(side):
            dist.broadcast(buf[:m], src=0, group=g)
    sent += m
st(ev='loop_done', rank={rank}, sent=sent)
if {str(a.skip_sender_sync)} == "True" and {rank} == 0:
    st(ev='sender_skips_sync', rank={rank})
    dt = time.time() - t0
    st(ev='stream', rank={rank}, GiB=round(n/2**30, 3), s=round(dt, 4),
       GBps=round(n/dt/1e9, 2), note='issue-only')
    raise SystemExit(0)
if side is None:
    torch.cuda.synchronize()
else:
    torch.cuda.current_stream().wait_stream(side)
    side.synchronize()
dt = time.time() - t0
st(ev='first_stream_done', rank={rank})
st(ev='stream', rank={rank}, GiB=round(n/2**30, 3), s=round(dt, 4), GBps=round(n/dt/1e9, 2))
"""
        path = f"{REPO}/codex_coding/results/v0.1.23.3/task2/live_graph/gpu_r01/probe/{tag}_r{rank}.py"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(code)
        env = ("PYTHONPATH=main_lib LD_LIBRARY_PATH=/opt/hyhal/lib:$LD_LIBRARY_PATH "
               "NCCL_NET_PLUGIN=librccl-net-shca.so NCCL_SOCKET_IFNAME=ib0 NCCL_DEBUG=WARN")
        cmd = f"docker exec -d {CONTAINER[node]} bash -lc 'cd {REPO}; export {env}; ulimit -c 0; " \
              f"HIP_VISIBLE_DEVICES={g} python3 {path} > /root/{tag}_r{rank}.log 2>&1'"
        subprocess.check_call(["ssh", "-n", "-o", "BatchMode=yes", node, cmd])
        stamp(ev="launched", node=node, rank=rank)
    return 0


if __name__ == "__main__":
    sys.exit(main())
