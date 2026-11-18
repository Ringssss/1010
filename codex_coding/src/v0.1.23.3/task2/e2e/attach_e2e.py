"""End-to-end activation through a real SGLang engine: restore -> IPC attach -> first token.

  attach_e2e.py <manifest_dir> <out_dir> --model qwen3-0.6b --node node28 --card 6 [--reps 3]

Run INSIDE the node's pool container (zjn-fluid-pool-<nn>; HIP IPC is node-local), with the four
node daemons running (pool_ctl.sh start). Steps, all logged to <out_dir>/attach_e2e.jsonl:
  0. the card's noded arena is released (it would not leave room for an engine) and restores go
     to fresh device memory (use_arena=False);
  1. reference: an engine that loads the checkpoint from disk, greedy output of a fixed prompt;
  2. an engine started with --load-format dummy + the FluidPool custom weight loader
     (its output before attach is checked to differ: the weights really are dummy);
  3. per rep: Pool.restore of the model onto the card (sources: DRAM copies parked on the other
     nodes), describe_job, update_weights_from_tensor (attach), job_release, then a 1-token
     generate (first token) and the full greedy output, compared with the reference.
Reported per rep: restore_s, attach_s (engine call wall time; loader's own split in the log),
first_token_s, activation_ttft_s = restore_s + attach_s + first_token_s, match.
"""
import argparse
import json
import os
import time

import torch  # noqa: F401  (CUDA init order: before sglang)

from fluidckpt.live.pool import GiB, Pool
from fluidckpt.serve import sglang_attach as SA

NODES = {"node27": "12.11.3.27", "node28": "12.11.3.28", "node29": "12.11.3.29", "node30": "12.11.3.30"}
DURABLE = {"qwen3-0.6b": "node28", "9g-8b": "node28", "qwen36-35b": "node28", "clover-35b": "node30"}
PROMPT = "The three most important properties of a distributed storage system are"

ap = argparse.ArgumentParser()
ap.add_argument("manifests")
ap.add_argument("out")
ap.add_argument("--model", default="qwen3-0.6b")
ap.add_argument("--node", default="node28")
ap.add_argument("--card", type=int, default=6)
ap.add_argument("--reps", type=int, default=3)
ap.add_argument("--max-new", type=int, default=32)
ap.add_argument("--mem-frac", type=float, default=0.45)
ap.add_argument("--no-reference", action="store_true")
ap.add_argument("--restore-arena-gib", type=float, default=48, help="re-create the card's arena at the end (0: leave)")
logp = attach_log = a = None


def say(**k):
    k.setdefault("t", round(time.time(), 3))
    line = json.dumps(k)
    with open(logp, "a") as f:
        f.write(line + "\n")
    print(line[:400], flush=True)


def main():
    """Everything runs under the __main__ guard: SGLang starts its workers with spawn, which
    re-imports this module."""
    global a, logp, attach_log
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    logp = os.path.join(a.out, "attach_e2e.jsonl")
    attach_log = os.path.join(a.out, "attach_loader.jsonl")



    import sglang as sgl  # noqa: E402

    man = json.load(open(os.path.join(a.manifests, a.model + ".json")))
    model_dir = man["build"]["model_dir"]
    say(event="config", **vars(a), model_dir=model_dir)
    greedy = {"temperature": 0.0, "max_new_tokens": a.max_new}


    def engine(**kw):
        t = time.time()
        e = sgl.Engine(model_path=model_dir, tp_size=1, base_gpu_id=a.card, mem_fraction_static=a.mem_frac,
                       log_level="error", attention_backend="triton", page_size=1,   # gfx936: as in
                       disable_cuda_graph=True, **kw)                                 # native_loading.py
        return e, time.time() - t


    def gen(e, n=None):
        sp = dict(greedy) if n is None else {"temperature": 0.0, "max_new_tokens": n}
        t = time.time()
        o = e.generate(PROMPT, sp)
        return o, time.time() - t


    def ids(o):
        return o.get("output_ids") or o.get("meta_info", {}).get("output_ids")


    pool = Pool(NODES, log=lambda **k: None)
    pool.start(react=False)
    pool.register(a.model, os.path.join(a.manifests, a.model + ".json"), DURABLE[a.model])
    cl = pool.rpc[a.node]
    try:
        cl.call("arena", card=a.card, gib=0)
        say(event="arena_released", card=a.card)
    except Exception as e:                                   # noqa: BLE001
        say(event="arena_release_failed", card=a.card, error=repr(e)[:200])

    ref = None
    if not a.no_reference:
        e, s = engine()
        o, dt = gen(e)
        ref = {"text": o["text"], "ids": ids(o)}
        say(event="reference", engine_start_s=round(s, 2), gen_s=round(dt, 3), text=o["text"][:200])
        e.shutdown()

    e, s = engine(load_format="dummy", custom_weight_loader=[SA.LOADER])
    o, _ = gen(e)
    say(event="dummy_engine", engine_start_s=round(s, 2), differs_from_reference=None if ref is None else o["text"] != ref["text"],
        text=o["text"][:120])

    p = pool.park(a.model, copies=1, tiers=("dram",), avoid_nodes=(a.node,))
    say(event="parked_remote", GiB=p.get("GiB"), short=p.get("short"))
    for k in range(a.reps):
        r = pool.restore(a.model, a.node, [a.card], allow=("dram", "file"), release=False, use_arena=False)
        desc = SA.describe_job(pool, a.model, a.node, [a.card], r["job"], log_path=attach_log)
        t = time.time()
        res = e.update_weights_from_tensor(SA.request(desc), load_format=SA.LOADER)
        attach_s = time.time() - t
        cl.call("job_release", job=r["job"])
        o1, ft = gen(e, 1)
        o, _ = gen(e)
        match = None if ref is None else (o["text"] == ref["text"] and (ids(o) == ref["ids"] if ref["ids"] else True))
        say(event="activation", rep=k, GiB=r["GiB"], restore_s=r["total_s"], restore_GBps=r["GBps"],
            attach_s=round(attach_s, 4), first_token_s=round(ft, 4),
            activation_ttft_s=round(r["total_s"] + attach_s + ft, 4), match=match, update_result=str(res)[:120],
            paths_GiB={kk: round(v["bytes"] / GiB, 2) for kk, v in r["paths"].items()})
        if match is False:
            say(event="MISMATCH", text=o["text"][:300], ref=ref["text"][:300])
    e.shutdown()
    if a.restore_arena_gib > 0:
        cl.call("arena", card=a.card, gib=a.restore_arena_gib)
    say(event="done")
    pool.stop()


if __name__ == "__main__":
    main()
