"""SGLang-style zero-copy keep-alive: weights stay in HBM in a keeper process; a second process
maps them through a HIP IPC handle. Measures the attach itself and checks the mapped bytes.

Run inside the destination node's container (the consumer must be on the node that owns the card):
  ipc_attach_bench.py <manifest_dir> <model> <card> <out_dir>
The node daemon is the keeper (it restores the model and exports the handle)."""
import base64, hashlib, json, os, sys, time
import torch
from fluidckpt.live.pool import GiB, Pool

NODES = {"node27": "12.11.3.27", "node28": "12.11.3.28", "node29": "12.11.3.29", "node30": "12.11.3.30"}
man, name, card, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
os.makedirs(out, exist_ok=True)
logf = open(os.path.join(out, "ipc_attach.log"), "a")
def say(**k):
    line = json.dumps(k); logf.write(line + "\n"); logf.flush(); print(line[:300], flush=True)

pool = Pool(NODES, log=lambda **k: None); pool.start(react=False)
m = pool.register(name, f"{man}/{name}.json", "node28")
r = pool.restore(name, "node28", [card], allow=("file",), release=False)
say(event="keeper_loaded", model=name, GiB=r["GiB"], cold_load_s=r["total_s"])
exp = pool.rpc["node28"].call("job_export", job=r["job"])[str(card)]
handle = tuple(base64.b64decode(x) if isinstance(x, str) else x for x in exp["handle"])
dev = f"cuda:{card}"
t = time.time(); torch.zeros(1, device=dev); say(event="consumer_context", s=round(time.time() - t, 2))
base, _ = pool.layout(m, [card])
c0 = m.chunks[0]; off0 = base[c0.file][1] + c0.offset
cl = m.chunks[-1]; offl = base[cl.file][1] + cl.offset
times = []
for i in range(20):
    t = time.perf_counter()
    st = torch.UntypedStorage._new_shared_cuda(*handle)
    x = torch.empty(0, dtype=torch.uint8, device=dev).set_(st)[exp["offset"]:exp["offset"] + exp["nbytes"]]
    first = int(x[off0]); torch.cuda.synchronize(dev)       # the mapping is usable
    times.append(time.perf_counter() - t)
    if i == 0:
        ok0 = hashlib.sha256(x[off0:off0 + c0.nbytes].cpu().numpy()).hexdigest() == c0.id
        okl = hashlib.sha256(x[offl:offl + cl.nbytes].cpu().numpy()).hexdigest() == cl.id
        say(event="mapped_bytes_match", first_chunk=ok0, last_chunk=okl, mapped_GiB=round(x.numel() / GiB, 2))
    del x, st
times.sort()
say(event="ipc_attach", model=name, GiB=r["GiB"], n=len(times), first_ms=round(times[-1] * 1e3, 3),
    median_ms=round(times[len(times) // 2] * 1e3, 3), min_ms=round(times[0] * 1e3, 3))
pool.rpc["node28"].call("job_release", job=r["job"]); pool.stop()
