"""Open-loop, timestamped replay through real SGLang servers: TTFT and SLO attainment.

SKELETON -- written while the cluster was busy; not yet run. Run inside one node's pool container
with the node daemons up:
  replay_ttft.py <manifest_dir> <out_dir> --node node28 --cards 4,5,6,7 --models qwen3-0.6b,9g-8b
                 --resident 1 --speedup 600 --requests 200 --slo 1.0,2.0,5.0

Model: one SGLang HTTP server per model, each on its own card, started once with
--load-format dummy and the FluidPool custom weight loader (a warm runtime pool; engine start-up
is outside the measured path for every system alike). At most --resident models hold valid
weights at a time (LRU) -- the stand-in for HBM pressure, since release/resume of the engine's
memory is a no-op without torch_memory_saver on this image. A request for a non-resident model
is an activation: Pool.restore onto the server's card + attach (/update_weights_from_tensor with
the descriptor) and only then the request is sent. TTFT = first streamed token - arrival time
(queueing and activation included). Arrivals: gmt_create of the GenAI trace, compressed by
--speedup; model ids mapped to our catalog by popularity rank (as maas3_run.requests()).

--system picks what the activation reads: "fluidpool" (whatever the pool holds: DRAM/HBM all
over the cluster), "local_dram" (only this node's DRAM copy, Janus/SGLang-style), "disk" (the
durable file). The attach path is the same for all three.
"""
import argparse
import asyncio
import collections
import csv
import json
import os
import subprocess
import time

TRACE = "/home/zhujianian/clusterdata/cluster-trace-v2026-GenAI/lora_request_trace.csv"
NODES = {"node27": "12.11.3.27", "node28": "12.11.3.28", "node29": "12.11.3.29", "node30": "12.11.3.30"}
DURABLE = {"qwen3-0.6b": "node28", "9g-8b": "node28", "qwen36-35b": "node28", "clover-35b": "node30"}
PROMPT = "Summarize the following request in one sentence: "

ap = argparse.ArgumentParser()
ap.add_argument("manifests")
ap.add_argument("out")
ap.add_argument("--node", default="node28")
ap.add_argument("--cards", default="4,5")
ap.add_argument("--models", default="qwen3-0.6b,9g-8b")
ap.add_argument("--resident", type=int, default=1)
ap.add_argument("--system", choices=["fluidpool", "local_dram", "disk"], default="fluidpool")
ap.add_argument("--speedup", type=float, default=600.0)
ap.add_argument("--requests", type=int, default=200)
ap.add_argument("--max-new", type=int, default=16)
ap.add_argument("--slo", default="0.5,1.0,2.0,5.0")
ap.add_argument("--port0", type=int, default=31000)
ap.add_argument("--mem-frac", type=float, default=0.4)
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
logp = os.path.join(a.out, f"replay_{a.system}.jsonl")


def say(**k):
    k.setdefault("t", round(time.time(), 3))
    with open(logp, "a") as f:
        f.write(json.dumps(k) + "\n")


def arrivals(models, n):
    rows = sorted(csv.DictReader(open(TRACE)), key=lambda r: r["gmt_create"])
    cnt = collections.Counter(r["checkpoint_model_version_id"] for r in rows)
    name = dict(zip([m for m, _ in cnt.most_common(len(models))], models))
    t0, out = None, []
    for r in rows:
        m = name.get(r["checkpoint_model_version_id"])
        if m is None:
            continue
        ts = time.mktime(time.strptime(r["gmt_create"], "%Y-%m-%d %H:%M:%S"))
        t0 = ts if t0 is None else t0
        out.append(((ts - t0) / a.speedup, m, int(float(r["prompt_length"] or 32))))
        if len(out) >= n:
            break
    return out


def start_servers(models, cards):
    from fluidckpt.serve import sglang_attach as SA
    procs = {}
    for i, (m, c) in enumerate(zip(models, cards)):
        man = json.load(open(os.path.join(a.manifests, m + ".json")))
        port = a.port0 + i
        cmd = ["python3", "-m", "sglang.launch_server", "--model-path", man["build"]["model_dir"],
               "--base-gpu-id", str(c), "--tp-size", "1", "--port", str(port), "--load-format", "dummy",
               "--custom-weight-loader", SA.LOADER, "--mem-fraction-static", str(a.mem_frac), "--log-level", "error"]
        procs[m] = (subprocess.Popen(cmd, stdout=open(os.path.join(a.out, f"server_{m}.log"), "w"),
                                     stderr=subprocess.STDOUT), port, c)
    return procs


async def wait_ready(session, port, timeout=900):
    t = time.time()
    while time.time() - t < timeout:
        try:
            async with session.get(f"http://127.0.0.1:{port}/health") as r:
                if r.status == 200:
                    return True
        except Exception:                                # noqa: BLE001
            pass
        await asyncio.sleep(2)
    raise TimeoutError(f"server on port {port} not ready")


async def attach(session, port, desc_path):
    from sglang.srt.utils import MultiprocessingSerializer
    from fluidckpt.serve import sglang_attach as SA
    payload = {"serialized_named_tensors": [MultiprocessingSerializer.serialize(SA.request(desc_path), output_str=True)],
               "load_format": SA.LOADER, "flush_cache": True}
    async with session.post(f"http://127.0.0.1:{port}/update_weights_from_tensor", json=payload) as r:
        return r.status, await r.text()


async def first_token(session, port, prompt_len):
    body = {"text": PROMPT + "x " * max(1, prompt_len // 2), "stream": True,
            "sampling_params": {"temperature": 0.0, "max_new_tokens": a.max_new}}
    t = time.time()
    first = None
    async with session.post(f"http://127.0.0.1:{port}/generate", json=body) as r:
        async for line in r.content:
            if first is None and line.strip().startswith(b"data:") and b"[DONE]" not in line:
                first = time.time()
    return first, time.time() - t


async def main():
    import aiohttp
    from fluidckpt.live.pool import Pool
    from fluidckpt.serve import sglang_attach as SA
    models, cards = a.models.split(","), [int(c) for c in a.cards.split(",")]
    pool = Pool(NODES, log=lambda **k: None)
    pool.start(react=False)
    for m in models:
        pool.register(m, os.path.join(a.manifests, m + ".json"), DURABLE[m])
        pool.rpc[a.node].call("arena", card=cards[models.index(m)], gib=0)
        if a.system == "fluidpool":
            pool.park(m, copies=1, tiers=("dram",), avoid_nodes=(a.node,))
        elif a.system == "local_dram":
            pool.park(m, copies=1, tiers=("dram",), within=(a.node,))
    allow = {"fluidpool": ("dram", "hbm", "ssd", "file"), "local_dram": ("dram",), "disk": ("file",)}[a.system]
    procs = start_servers(models, cards)
    resident = collections.OrderedDict()
    locks = {m: asyncio.Lock() for m in models}
    recs = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3600)) as session:
        await asyncio.gather(*(wait_ready(session, p) for _, p, _ in procs.values()))
        say(event="servers_ready", models=models, cards=cards)
        reqs = arrivals(models, a.requests)
        t0 = time.time()

        async def one(i, at, m, plen):
            await asyncio.sleep(max(0.0, t0 + at - time.time()))
            arrive = time.time()
            _, port, card = procs[m]
            act = None
            async with locks[m]:
                if m not in resident:
                    while len(resident) >= a.resident:
                        resident.popitem(last=False)     # its weights count as gone
                    loop = asyncio.get_running_loop()
                    r = await loop.run_in_executor(None, lambda: pool.restore(
                        m, a.node, [card], allow=allow, release=False, use_arena=False))
                    desc = SA.describe_job(pool, m, a.node, [card], r["job"], log_path=os.path.join(a.out, "attach_loader.jsonl"))
                    ta = time.time()
                    st, _ = await attach(session, port, desc)
                    pool.rpc[a.node].call("job_release", job=r["job"])
                    act = {"restore_s": r["total_s"], "attach_s": round(time.time() - ta, 4), "attach_status": st}
                    resident[m] = True
                resident.move_to_end(m)
            first, total = await first_token(session, port, plen)
            rec = {"event": "request", "i": i, "model": m, "arrival_rel_s": round(arrive - t0, 3),
                   "ttft_s": None if first is None else round(first - arrive, 4), "e2e_s": round(time.time() - arrive, 4),
                   "activation": act}
            recs.append(rec)
            say(**rec)

        await asyncio.gather(*(one(i, at, m, pl) for i, (at, m, pl) in enumerate(reqs)))
    slos = [float(x) for x in a.slo.split(",")]
    tt = sorted(r["ttft_s"] for r in recs if r["ttft_s"] is not None)
    summ = {"system": a.system, "n": len(recs), "activations": sum(1 for r in recs if r["activation"]),
            "ttft_p50": tt[len(tt) // 2] if tt else None, "ttft_p99": tt[int(0.99 * (len(tt) - 1))] if tt else None,
            "slo_attainment": {str(s): round(sum(1 for x in tt if x <= s) / max(1, len(recs)), 4) for s in slos}}
    say(event="summary", **summ)
    json.dump(summ, open(os.path.join(a.out, f"summary_{a.system}.json"), "w"), indent=1)
    for p, _, _ in procs.values():
        p.terminate()
    pool.stop()


if __name__ == "__main__":
    asyncio.run(main())
