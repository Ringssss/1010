"""Arm F, end to end: a capture snapshot kept alive in DRAM outlives its producer, then flows.

The point of the keepalive is the second half of this script, so the first half exists only to
create something worth keeping alive: one cold load in `capture` mode, which reads the safetensors
once (that is arm B1's 17 s, paid off the critical path) and hands the post-processed shard to the
keeper, which copies it into its own pinned DRAM. The producer is then killed, and every later
measurement happens with no process owning those weights.

Why the source snapshot must be a *capture* and not an `ingest`
--------------------------------------------------------------
`ingest` partitions the raw safetensors and packs them in HuggingFace naming; a `capture` exports
what the loaded model actually holds. Those are different objects -- 1045 raw names (33.93 GiB)
against 1039 post-processed storages (32.75 GiB) -- and their name sets do not intersect at all:
the loader fuses `in_proj_a`/`in_proj_b` into `in_proj_ba`, folds `kv_b_proj` into the MLA
attributes, and adds a `gemma_weight` per norm. A restore target builds its skeleton in the
post-processed layout, so an ingest snapshot can never fill it; the 30 derived attributes are
simply absent from it. Feeding one to `HbmTarget` fails the metadata compare before a byte moves.

Order of operations is forced by what a restore *is*: a destination is only fillable once it has
published a skeleton, because `HbmTarget` maps the destination's own storages over IPC. So the
target comes up first (in `restore` mode, `--load-format dummy`) and blocks in `wait_skeleton`;
only then can the keeper write bytes into it.

Reported number: max(weights_ready) - min(load_begin) over the destination ranks -- the same
interval B1 and B2 are scored on.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = "/home/zhujianian/zhujianian/fluidckpt_zjn"
CLUSTER = os.path.join(ROOT, "codex_coding/src/task2/cluster")
for p in (HERE, CLUSTER, os.path.join(ROOT, "main_lib")):
    if p not in sys.path:
        sys.path.insert(0, p)

import keepctl  # noqa: E402
from fluidckpt.transport.flowplan import Copy, Demand, route, solve, to_ops  # noqa: E402

MODEL = "/data/node28/shared/zhujianian/Qwen3.6-35B-A3B"
LAUNCHER = os.path.join(ROOT, "codex_coding/src/serving/sglang_fluid_launch.py")
SERVER_PORT = 31842
PRODUCER_PORT = 31843
GB = 1e9
HANDOFF = "/dev/shm/fluidckpt"
PROMPTS = ["The capital of France is", "Question: What is 2 + 3?\nAnswer:"]


def sh(node, cmd, timeout=3600):
    """One shell command on a mesh node (node27 is local, the rest go over ssh)."""
    return keepctl._ssh(node, cmd, timeout=timeout)


def get(port, path, timeout=5, host="127.0.0.1"):
    with urllib.request.urlopen(f"http://{host}:{port}/{path}", timeout=timeout) as r:
        p = r.read()
    return json.loads(p) if p else {}


def post(port, path, body, timeout=600, host="127.0.0.1"):
    req = urllib.request.Request(f"http://{host}:{port}/{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def dexec(node, inner, timeout=3600):
    c = keepctl.CONTAINER[node]
    return sh(node, f"docker exec -w {ROOT} {c} bash -lc {shlex.quote(inner)}", timeout=timeout)


def server_args(port, mem_frac, host="0.0.0.0", load_format=None):
    args = ["python3", LAUNCHER, "--model-path", MODEL, "--host", host,
            "--port", str(port), "--tp-size", "2", "--mem-fraction-static", mem_frac,
            "--context-length", "4096", "--max-running-requests", "4", "--disable-cuda-graph",
            "--attention-backend", "triton", "--page-size", "1", "--moe-runner-backend", "triton",
            "--max-total-tokens", "8192", "--disable-custom-all-reduce",
            "--mamba-scheduler-strategy", "extra_buffer", "--max-mamba-cache-size", "32",
            "--random-seed", "42"]
    if load_format:
        args += ["--load-format", load_format]
    return args


def write_spec(node, outdir, name, command, env, log):
    spec = {"command": command, "env": env, "cwd": ROOT, "log": log}
    spec_path = os.path.join(outdir, f"{name}.process.json")
    with open(spec_path, "w") as f:
        json.dump(spec, f)
    state = spec_path.replace(".json", ".pid.json")
    if os.path.exists(state):
        os.remove(state)      # process.py refuses to start while a state file is present
    return spec_path


def proc_start(node, spec_path):
    return dexec(node, f"source /opt/dtk/env.sh >/dev/null 2>&1; ulimit -c 0; "
                       f"python3 codex_coding/src/task2/cluster/process.py start --spec {spec_path}")


def proc_stop(node, spec_path, timeout=180):
    try:
        return dexec(node, f"ulimit -c 0; "
                           f"python3 codex_coding/src/task2/cluster/process.py stop --spec {spec_path}",
                     timeout=timeout)
    except Exception:
        return None


def base_env(**kw):
    env = {"PYTHONPATH": os.path.join(ROOT, "main_lib"), "HIP_VISIBLE_DEVICES": "0,1",
           "HSA_FORCE_FINE_GRAIN_PCIE": "1", "AMD_SERIALIZE_KERNEL": "3",
           "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
           "FLUIDCKPT_HANDOFF": HANDOFF, "FLUIDCKPT_TP_SIZE": "2",
           "SGLANG_KV_LAYOUT_HCU_FA": "false", "FLUIDCKPT_TIMEOUT": "2400"}
    env.update(kw)
    return env


def wait_target(port, node, timeout=1800):
    t0 = time.monotonic()
    ip = keepctl.IP[node]
    while time.monotonic() - t0 < timeout:
        try:
            get(port, "health", timeout=3, host=ip)
            return time.monotonic() - t0
        except Exception:
            time.sleep(1)
    raise TimeoutError(f"target on {node}:{port} never became healthy")


def keeper_capture(node, gpus, snap, timeout=7200):
    """Tell each source keeper to arm on the capture export and copy the shard into its own DRAM.

    This call is what `FLUIDCKPT_CAPTURE_WAIT=1` is waiting for: the producer exports the shard and
    then blocks in `_wait_done(suffix="captured")`, which is only released once the keeper has
    finished the D2H copy and written `rank{tp}.captured`. B2 never needs this because `attach`
    reads the producer's own HBM; a keepalive snapshot lives in the keeper, so the keeper has to be
    told explicitly to take it.

    `capture` blocks for pin + D2H (tens of seconds per rank), so the caller runs it on a side
    thread while the producer's health and the `captured` files are polled from the main thread.
    """
    from concurrent.futures import ThreadPoolExecutor
    base = keepctl.PORT_BASE
    with ThreadPoolExecutor(len(gpus)) as ex:
        futs = {(node, g): ex.submit(post, base + g, "capture", {"tag": snap, "snap": snap},
                                     timeout, keepctl.IP[node]) for g in gpus}
        out = {}
        for k, f in futs.items():
            try:
                out[f"{k[0]}:{k[1]}"] = f.result()
            except Exception as e:
                out[f"{k[0]}:{k[1]}"] = {"error": repr(e)}
        return out


def wait_health(port, node, timeout=1800):
    """Poll `health` until it answers, like wait_target but with the port on another node.

    A bare `get()` here is wrong and cost one run: the server is not listening yet for most of its
    start-up, so the first request is refused and the whole driver aborts while the producer it just
    launched is still loading. Refusal is the expected state for the first minute, not a failure.
    """
    t0 = time.monotonic()
    ip = keepctl.IP[node]
    while time.monotonic() - t0 < timeout:
        try:
            get(port, "health", timeout=3, host=ip)
            return time.monotonic() - t0
        except Exception:
            time.sleep(1)
    raise TimeoutError(f"node {node}:{port} never became healthy")


def summary_load(metrics, ranks=2):
    """max(weights_ready) - min(load_begin) over the ranks of this run."""
    begins, ends = {}, {}
    for r in range(ranks):
        path = os.path.join(metrics, f"rank{r}.jsonl")
        if not os.path.exists(path):
            continue
        for line in open(path):
            e = json.loads(line)
            if e["event"] in ("load_begin", "model_layout_begin"):
                begins[r] = min(begins.get(r, 1 << 62), e["monotonic_ns"])
            if e["event"] == "weights_ready":
                ends[r] = e["monotonic_ns"]
    if not begins or not ends:
        return None
    return {"load_s": (max(ends.values()) - min(begins.values())) / 1e9,
            "ranks_begin": len(begins), "ranks_end": len(ends),
            "per_rank_s": {r: (ends[r] - begins[r]) / 1e9 for r in ends if r in begins}}


def stage_derived(node, src_dir, tag):
    """Put the capture's derived manifest where the target's hook looks for it.

    `_install_derived` looks under `$FLUIDCKPT_HANDOFF/<tag>`, and that root is `/dev/shm` -- a
    per-node tmpfs, not a shared filesystem. So the manifest has to cross nodes, and the cheap way
    is to stage it on the shared repo mount (visible from every node) and `cp` it into the target's
    tmpfs. A base64 pipe through ssh -> docker exec loses to shell quoting; two commands over a
    shared path have nothing to lose.
    """
    names = [n for n in ("derived.json", "derived.rank0.json", "derived.rank1.json")
             if os.path.exists(os.path.join(src_dir, n))]
    if not names:
        return {"staged": 0}
    stage = os.path.join(ROOT, "codex_coding/results/v0.2.0/_derived_stage")
    os.makedirs(stage, exist_ok=True)
    for n in names:
        shutil.copyfile(os.path.join(src_dir, n), os.path.join(stage, n))
    dst = os.path.join(HANDOFF, tag)
    out = dexec(node, f"mkdir -p {dst} && cp {stage}/*.json {dst}/ && ls {dst}", timeout=120)
    return {"staged": len(names), "target_dir": out.strip().splitlines()[-1] if out.strip() else ""}


def require_source(rep, snap):
    """The keeper must already hold a capture of `snap`; we never build one here on the hot path."""
    try:
        st = json.loads(sh("node27", f"curl -s http://127.0.0.1:39700/status", timeout=60))
    except Exception:
        return None
    return st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--snap", default="qwen36cap")
    ap.add_argument("--src", default="node27")
    ap.add_argument("--src-gpus", default="0,1")
    ap.add_argument("--dst", default="node29")
    ap.add_argument("--dst-gpus", default="0,1")
    ap.add_argument("--tag", default="restore_f4")
    ap.add_argument("--mem-fraction", default="0.70")
    ap.add_argument("--producer-mem-fraction", default="0.68")
    ap.add_argument("--skip-capture", action="store_true")
    ap.add_argument("--answer-only", action="store_true", help="solve the LP only; touch no server")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    src_gpus = [int(x) for x in args.src_gpus.split(",")]
    dst_gpus = [int(x) for x in args.dst_gpus.split(",")]
    members = keepctl.parse_mesh()
    rep = {"arm": "F_keepalive_end_to_end", "model": MODEL, "snap": args.snap,
           "src": args.src, "dst": args.dst, "tag": args.tag}
    target_up = producer_up = False
    p_spec = t_spec = None
    cap_metrics = os.path.join(args.out, "metrics_producer")
    try:
        # ---- 0. the thing being kept alive: one cold load, captured into the keeper's pinned DRAM.
        if not args.skip_capture and not args.answer_only:
            p_spec = write_spec(
                args.src, args.out, "producer",
                server_args(PRODUCER_PORT, args.producer_mem_fraction, host="127.0.0.1"),
                base_env(FLUIDCKPT_MODE="capture", FLUIDCKPT_TAG=args.snap,
                         FLUIDCKPT_METRICS=cap_metrics, FLUIDCKPT_CAPTURE_WAIT="1"),
                os.path.join(args.out, "producer.log"))
            t0 = time.time()
            rep["producer_start"] = proc_start(args.src, p_spec)
            producer_up = True
            rep["producer_ready_s"] = wait_health(PRODUCER_PORT, args.src, timeout=1800)
            rep["producer_load_s"] = summary_load(cap_metrics)
            # Arm the keepers as soon as the producer is up. `capture` blocks internally until the
            # export appears, so it has to race the export rather than follow it -- and it must be
            # launched before the wait loop, or that loop waits on a file nobody is going to write.
            from concurrent.futures import ThreadPoolExecutor
            _cap_pool = ThreadPoolExecutor(1)
            cap_fut = _cap_pool.submit(keeper_capture, args.src, src_gpus, args.snap)
            # The keeper copies the shard into its own DRAM and writes `captured`; the producer
            # holds its HBM until that lands (FLUIDCKPT_CAPTURE_WAIT=1), so no forward can run and
            # crash the process before the snapshot is safe.
            t1 = time.time()
            while time.time() - t1 < 1800:
                try:
                    ls = dexec(args.src, f"ls {HANDOFF}/{args.snap}/", timeout=60)
                except Exception:
                    ls = ""
                if all(f"rank{r}.captured" in ls for r in (0, 1)):
                    break
                time.sleep(0.5)
            rep["capture_wait_s"] = time.time() - t1
            try:
                rep["keeper_capture"] = cap_fut.result(timeout=1800)
            except Exception as e:
                rep["keeper_capture"] = {"error": repr(e)}
            _cap_pool.shutdown(wait=False)
            rep["producer_replies"] = [
                post(PRODUCER_PORT, "generate",
                     {"text": p, "sampling_params": {"temperature": 0, "max_new_tokens": 24},
                      "return_logprob": True, "logprob_start_len": -1}, host=keepctl.IP[args.src])
                for p in PROMPTS]
            with open(os.path.join(args.out, "summary.json"), "w") as f:
                json.dump(rep, f, indent=2)

        # ---- 1. destination skeleton. Nothing can be written until this exists.
        if not args.answer_only:
            if producer_up:
                proc_stop(args.src, p_spec)
                producer_up = False
                rep["producer_stopped_before_target"] = True
            rep["derived_staged"] = stage_derived(args.dst, os.path.join(HANDOFF, args.snap), args.tag)
            t_spec = write_spec(
                args.dst, args.out, "target",
                server_args(SERVER_PORT, args.mem_fraction, load_format="dummy"),
                base_env(FLUIDCKPT_MODE="restore", FLUIDCKPT_TAG=args.tag,
                         FLUIDCKPT_METRICS=os.path.join(args.out, "metrics_target"),
                         FLUIDCKPT_MANIFEST=os.path.join(HANDOFF, args.tag, "derived.json")),
                os.path.join(args.out, "target.log"))
            rep["target_start"] = proc_start(args.dst, t_spec)
            target_up = True
            # The target blocks inside load_model until the keeper fills it, so health only answers
            # after the flow has completed. Wait for the exported skeleton instead.
            t2 = time.time()
            while time.time() - t2 < 1800:
                try:
                    ls = dexec(args.dst, f"ls {HANDOFF}/{args.tag}/rank0.pkl {HANDOFF}/{args.tag}/rank1.pkl",
                               timeout=60)
                    if "rank1.pkl" in ls:
                        break
                except Exception:
                    pass
                time.sleep(2)
            rep["skeleton_ready_s"] = time.time() - t2

        # ---- 2. solve. The source's packed layout is what the LP sizes each demand from.
        index, meta = {}, {}
        for tp, g in enumerate(src_gpus):
            r = keepctl.call(args.src, g, "index", {"snap": args.snap, "tp": tp}, timeout=120)
            index[tp] = r["index"]
            meta[tp] = r["meta"]
        rep["shard_GiB"] = {str(tp): round(max(o + n for o, n in index[tp].values()) / 2**30, 2)
                            for tp in index}

        demands = []
        for tp, (dgpu, sgpu) in enumerate(zip(dst_gpus, src_gpus)):
            copies = [Copy(kind="dram", node=args.src, gpu=sgpu)]
            demands.append(Demand(tp=tp, node=args.dst, gpu=dgpu,
                                  nbytes=max(o + n for o, n in index[tp].values()), copies=copies))
        T, split, load = solve(demands)
        wid = lambda n, g: members.index((n, g))
        ops = to_ops(demands, split, wid)
        rep["plan"] = {"lp_T_s": T, "ops": len(ops),
                       "split_GB": [(i, j, round(b / GB, 3)) for i, j, b in split],
                       "route_of": [(i, route(demands[i].copies[j], demands[i])[0]) for i, j, _ in split],
                       "load_GBps": {k: round(v, 2) for k, v in sorted(load.items())}}

        # ---- 3. flow into the live target's HBM.
        if not args.answer_only:
            t_flow = time.time()
            body = {"ops": ops, "into": "hbm", "tag": args.tag, "snap": args.snap,
                    "retain_live": False,
                    "index": {str(tp): index[tp] for tp in index},
                    "meta": {str(tp): meta[tp] for tp in meta}}
            touched = {members[w] for o in ops for w in (o.get("src_w"), o["dst_w"])
                       if w is not None}
            res = keepctl.multi_call(members, "flow", body, only=touched, timeout=3600)
            rep["flow_wall_s"] = time.time() - t_flow
            rep["flow"] = {f"{k[0]}:{k[1]}": {kk: vv for kk, vv in v.items()
                                              if kk in ("total_s", "ib_s", "prep_s", "ib_bytes",
                                                        "h2d_bytes", "ssd_bytes", "local")}
                           for k, v in res.items() if isinstance(v, dict) and "error" not in v}
            rep["errors"] = {f"{k[0]}:{k[1]}": str(v.get("error"))[:400] for k, v in res.items()
                             if isinstance(v, dict) and "error" in v}

        # ---- 4. the target's first forward. Weights are only known good once text comes out.
        if target_up:
            rep["target_ready_s"] = wait_target(SERVER_PORT, args.dst, timeout=1800)
            rep["load_s"] = summary_load(os.path.join(args.out, "metrics_target"))
            rep["replies"] = [post(SERVER_PORT, "generate",
                                   {"text": p, "sampling_params": {"temperature": 0,
                                                                   "max_new_tokens": 24},
                                    "return_logprob": True, "logprob_start_len": -1},
                                   host=keepctl.IP[args.dst]) for p in PROMPTS]
            ref = rep.get("producer_replies")
            if ref:
                rep["output_matches_producer"] = [a.get("text") == b.get("text")
                                                  for a, b in zip(rep["replies"], ref)]
                rep["passed"] = bool(rep["output_matches_producer"]) and all(
                    rep["output_matches_producer"])
    finally:
        if producer_up and p_spec:
            proc_stop(args.src, p_spec)
        with open(os.path.join(args.out, "summary.json"), "w") as f:
            json.dump(rep, f, indent=2)
        print(json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()
