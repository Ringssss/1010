"""Arm B2: single-node IPC warm start off a LIVE producer, with no bytes copied.

Two SGLang servers share one pair of GPUs. The producer loads the checkpoint normally and exports
one HIP handle per post-processed storage; the consumer builds a meta skeleton (structure only, no
HBM of its own) and binds those handles in. This is the strongest single-node baseline because
nothing is read and nothing is copied -- the interval measured is skeleton construction alone.

Runs inside the keeper container on node27. Only processes started here are ever signalled.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = "/home/zhujianian/zhujianian/fluidckpt_zjn"
MODEL = "/data/node28/shared/zhujianian/Qwen3.6-35B-A3B"
LAUNCHER = os.path.join(ROOT, "codex_coding/src/serving/sglang_fluid_launch.py")
PROMPTS = ["The capital of France is", "Question: What is 2 + 3?\nAnswer:"]

PRODUCER_PORT = 31840
CONSUMER_PORT = 31841


def get(port, path, timeout=5):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/{path}", timeout=timeout) as r:
        p = r.read()
    return json.loads(p) if p else {}


def post(port, path, body, timeout=180):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/{path}",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def base_env(metrics, mode, handoff=None, tag="default"):
    env = dict(os.environ)
    env.update(PYTHONPATH=os.path.join(ROOT, "main_lib"),
               HIP_VISIBLE_DEVICES="0,1", HSA_FORCE_FINE_GRAIN_PCIE="1",
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
               SGLANG_KV_LAYOUT_HCU_FA="false", AMD_SERIALIZE_KERNEL="3",
               NCCL_NET_PLUGIN="librccl-net-shca.so",
               NCCL_DEBUG="WARN", FLUIDCKPT_MODE=mode,
               FLUIDCKPT_METRICS=metrics, FLUIDCKPT_TAG=tag,
               FLUIDCKPT_TIMEOUT="1800")
    if handoff:
        env["FLUIDCKPT_HANDOFF"] = handoff
    return env


def librccl_net():
    return "librccl-net-shca.so"


def server_args(port, mem_frac):
    return [sys.executable, LAUNCHER, "--model-path", MODEL, "--host", "127.0.0.1",
            "--port", str(port), "--tp-size", "2", "--mem-fraction-static", mem_frac,
            "--context-length", "4096", "--max-running-requests", "2", "--disable-cuda-graph",
            "--attention-backend", "triton", "--page-size", "1", "--moe-runner-backend", "triton",
            "--max-total-tokens", "4096", "--disable-custom-all-reduce",
            "--mamba-scheduler-strategy", "extra_buffer", "--max-mamba-cache-size", "32",
            "--random-seed", "42"]


def launch(name, args, env, outdir):
    log = open(os.path.join(outdir, f"{name}.log"), "w")
    p = subprocess.Popen(args, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                         start_new_session=True)
    log.close()
    return p


def stop(p):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(p.pid, sig)
        except ProcessLookupError:
            return
        try:
            p.wait(timeout=25)
            return
        except subprocess.TimeoutExpired:
            continue


def wait_http(p, port, timeout=1200):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if p.poll() is not None:
            raise RuntimeError(f"{port} exited rc={p.returncode}")
        try:
            get(port, "health", timeout=2)
            return time.monotonic() - t0
        except Exception:
            time.sleep(1)
    raise TimeoutError(f"{port} never came up")


def wait_files(paths, timeout=1800):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if all(os.path.exists(p) for p in paths):
            return time.monotonic() - t0
        time.sleep(0.2)
    raise TimeoutError("handoff files never appeared: " + repr(paths))


def generate(port):
    out = []
    for prompt in PROMPTS:
        r = post(port, "generate", {"text": prompt,
                                    "sampling_params": {"temperature": 0, "max_new_tokens": 24},
                                    "return_logprob": True, "logprob_start_len": -1})
        out.append({"prompt": prompt, "text": r.get("text"), "meta": r.get("meta_info", {}).get("completion_tokens")})
    return out


def summary_load(metrics, ranks=2):
    """max(weights_ready) - min(load_begin) over the ranks of this run."""
    begins, ends = {}, {}
    for r in range(ranks):
        path = os.path.join(metrics, f"rank{r}.jsonl")
        if not os.path.exists(path):
            continue
        for line in open(path):
            e = json.loads(line)
            if e["event"] == "load_begin":
                begins[r] = e["monotonic_ns"]
            if e["event"] == "weights_ready":
                ends[r] = e["monotonic_ns"]
    if not begins or not ends:
        return None
    return {"load_s": (max(ends.values()) - min(begins.values())) / 1e9,
            "ranks_begin": len(begins), "ranks_end": len(ends)}


def main():
    outdir = sys.argv[1]
    # Producer weights are 33.9 GiB/rank on a 64 GiB card, so its fraction must clear 0.53 before
    # any KV pool is reserved. The consumer holds no weights (meta skeleton) but still needs a pool.
    prod_frac = sys.argv[2] if len(sys.argv) > 2 else "0.68"
    cons_frac = sys.argv[3] if len(sys.argv) > 3 else "0.30"
    consumer_only = os.environ.get("B2_CONSUMER_ONLY") == "1"
    os.makedirs(outdir, exist_ok=True)
    handoff = os.path.join(outdir, "handoff")
    os.makedirs(handoff, exist_ok=True)
    rep = {"arm": "B2_ipc_attach", "model": MODEL, "producer_port": PRODUCER_PORT,
           "consumer_port": CONSUMER_PORT, "producer_mem_fraction": prod_frac,
           "consumer_mem_fraction": cons_frac}
    procs = []
    try:
        # ---- producer: a normal cold load, kept alive. Its load_s is also arm B1.
        p_metrics = os.path.join(outdir, "metrics_producer")
        if consumer_only:
            # The producer exported its handles and is still running from the previous attempt;
            # only the consumer path changed. Reuse both the process and its measurements.
            rep["producer_reused"] = True
            rep["producer_load_s"] = summary_load(p_metrics)
            prod = None
        else:
            env = base_env(p_metrics, "capture", handoff, tag="producer")
            t0 = time.monotonic()
            prod = launch("producer", server_args(PRODUCER_PORT, prod_frac), env, outdir)
            procs.append(prod)
            rep["producer_ready_s"] = wait_http(prod, PRODUCER_PORT)
            rep["producer_load_s"] = summary_load(p_metrics)
            rep["producer_wall_s"] = time.monotonic() - t0
            wait_files([os.path.join(handoff, "producer", f"rank{r}.pkl") for r in (0, 1)],
                       timeout=600)
            rep["producer_replies"] = generate(PRODUCER_PORT)
            with open(os.path.join(outdir, "summary.json"), "w") as f:
                json.dump(rep, f, indent=2)

        # ---- consumer: meta skeleton + IPC bind. Zero bytes moved.
        c_metrics = os.path.join(outdir, "metrics_consumer")
        env2 = base_env(c_metrics, "attach", handoff, tag="producer")
        env2["FLUIDCKPT_ATTACH_PKL"] = os.path.join(handoff, "producer")
        t1 = time.monotonic()
        cons = launch("consumer", server_args(CONSUMER_PORT, cons_frac) + ["--load-format", "dummy"],
                      env2, outdir)
        procs.append(cons)
        rep["consumer_ready_s"] = wait_http(cons, CONSUMER_PORT)
        rep["consumer_wall_s"] = time.monotonic() - t1
        rep["consumer_load_s"] = summary_load(c_metrics)
        rep["consumer_replies"] = generate(CONSUMER_PORT)
        same = [a["text"] == b["text"] for a, b in zip(rep["consumer_replies"], rep["producer_replies"])]
        rep["output_matches_producer"] = same
        rep["passed"] = bool(same) and all(same)
        with open(os.path.join(outdir, "summary.json"), "w") as f:
            json.dump(rep, f, indent=2)
    except BaseException as exc:
        rep["error"] = repr(exc)
        rep["passed"] = False
        with open(os.path.join(outdir, "summary.json"), "w") as f:
            json.dump(rep, f, indent=2)
        raise
    finally:
        for p in reversed(procs):
            stop(p)
        print(json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()
