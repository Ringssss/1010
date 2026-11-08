"""Arm F, end to end: keeper-filled restore of a snapshot that lives in another node's DRAM.

This is the keepalive path, and it is the one the whole design exists for. Nothing here needs a
live producer: the bytes were read off storage once, off the critical path, into pinned DRAM on the
source node, and they stay usable after the process that loaded them is gone.

The order of operations is forced by what a restore actually is. A destination only becomes
fillable once it has published a skeleton -- `HbmTarget` maps the destination's own storages over
IPC, so there is no address to write to until the target server has built its model and exported
one handle per storage. So the target comes up first and blocks in `wait_skeleton`/`_wait_done`;
only then does the keeper have somewhere to put the bytes.

Three stages, each measured separately:
  1. target skeleton -- `--load-format dummy` builds the empty model and exports its handles
  2. flow            -- `flowplan.solve()` picks the byte split across copies, `to_ops()` emits the
                        range ops, and the keepers move them (IB across nodes here)
  3. fill            -- the target's `_wait_done` returns and the server becomes ready

The reported number is max(weights_ready) - min(load_begin) over the destination ranks, the same
interval the other arms are scored on.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = "/home/zhujianian/zhujianian/fluidckpt_zjn"
CLUSTER = os.path.join(ROOT, "codex_coding/src/task2/cluster")
# `xfer.run` ships this file to /tmp/_x.py, so __file__ is not the repo copy -- the mesh helpers
# have to be found through the repo path, not relative to wherever the script happens to land.
for p in (HERE, CLUSTER, os.path.join(ROOT, "main_lib")):
    if p not in sys.path:
        sys.path.insert(0, p)

import keepctl  # noqa: E402
from fluidckpt.transport.flowplan import Copy, Demand, route, solve, to_ops  # noqa: E402

MODEL = "/data/node28/shared/zhujianian/Qwen3.6-35B-A3B"
LAUNCHER = os.path.join(ROOT, "codex_coding/src/serving/sglang_fluid_launch.py")
SERVER_PORT = 31842
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


def post(port, path, body, timeout=300, host="127.0.0.1"):
    req = urllib.request.Request(f"http://{host}:{port}/{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def server_args(port, mem_frac):
    return ["python3", LAUNCHER, "--model-path", MODEL, "--host", "0.0.0.0",
            "--port", str(port), "--tp-size", "2", "--mem-fraction-static", mem_frac,
            "--context-length", "4096", "--max-running-requests", "4", "--disable-cuda-graph",
            "--attention-backend", "triton", "--page-size", "1", "--moe-runner-backend", "triton",
            "--max-total-tokens", "8192", "--disable-custom-all-reduce",
            "--mamba-scheduler-strategy", "extra_buffer", "--max-mamba-cache-size", "32",
            "--random-seed", "42", "--load-format", "dummy"]


def target_spec(node, outdir, tag, mem_frac, spec_path):
    """Write the process spec for the restore-mode server and start it via `process.py`.

    Going through the spec file rather than a hand-built shell line matters for two reasons: the
    server lives on another node, so it has to be detached from this session (`start_new_session`
    inside process.py), and the argv contains model paths and quoting that a shell string mangles.
    The spec resolves both -- and `process.py stop` refuses to signal a PID whose start time moved,
    which is what keeps a stale PID from taking down an unrelated process.
    """
    spec = {
        "command": server_args(SERVER_PORT, mem_frac),
        "env": {
            "PYTHONPATH": os.path.join(ROOT, "main_lib"),
            "HIP_VISIBLE_DEVICES": "0,1",
            "FLUIDCKPT_MODE": "restore",
            "FLUIDCKPT_TAG": tag,
            "FLUIDCKPT_HANDOFF": HANDOFF,
            "FLUIDCKPT_METRICS": os.path.join(outdir, "metrics_target"),
            "FLUIDCKPT_TIMEOUT": "2400",
            "FLUIDCKPT_MANIFEST": os.path.join(HANDOFF, tag, "derived.json"),
            "FLUIDCKPT_TP_SIZE": "2",
            "HSA_FORCE_FINE_GRAIN_PCIE": "1",
            "AMD_SERIALIZE_KERNEL": "3",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        },
        "cwd": ROOT,
        "log": os.path.join(outdir, "target.log"),
    }
    with open(spec_path, "w") as f:
        json.dump(spec, f)
    for stale in (spec_path, spec_path.replace(".json", ".pid.json")):
        if os.path.exists(stale) and stale.endswith(".pid.json"):
            os.remove(stale)     # process.py refuses to start while a state file is present
    return spec_path


def start_target(node, outdir, tag, mem_frac="0.70"):
    spec_path = os.path.join(outdir, "target.process.json")
    target_spec(node, outdir, tag, mem_frac, spec_path)
    c = keepctl.CONTAINER[node]
    inner = (f"cd {ROOT} && source /opt/dtk/env.sh >/dev/null 2>&1; ulimit -c 0; "
             f"python3 codex_coding/src/task2/cluster/process.py start --spec {spec_path}")
    return sh(node, f"docker exec {c} bash -lc {json.dumps(inner)}")


def stop_target(node, outdir):
    spec_path = os.path.join(outdir, "target.process.json")
    if not os.path.exists(spec_path):
        return
    c = keepctl.CONTAINER[node]
    inner = (f"cd {ROOT} && ulimit -c 0; "
             f"python3 codex_coding/src/task2/cluster/process.py stop --spec {spec_path} || true")
    try:
        sh(node, f"docker exec {c} bash -lc {json.dumps(inner)}", timeout=120)
    except Exception:
        pass


def wait_target(port, node, timeout=900):
    t0 = time.monotonic()
    ip = keepctl.IP[node]
    while time.monotonic() - t0 < timeout:
        try:
            get(port, "health", timeout=3, host=ip)
            return time.monotonic() - t0
        except Exception:
            time.sleep(1)
    raise TimeoutError(f"target on {node}:{port} never became healthy")


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


def stage_derived(node, snap_handoff, tag, outdir):
    """Put the capture's derived manifest where the target's hook will look for it.

    The target allocates the MLA fused weights before it exports, and it can only know their shapes
    from the manifest the capture wrote. Two things force how this is done: `_install_derived` looks
    under `$FLUIDCKPT_HANDOFF/<tag>`, and the handoff root is `/dev/shm` -- a per-node tmpfs, not a
    shared filesystem. So the bytes have to cross nodes, and the obvious way to do that is to stage
    them on the shared repo mount (which every node sees) and `cp` them into the target's tmpfs
    afterwards. Building that transfer as a base64 pipe through ssh -> docker exec loses to shell
    quoting every time; two commands over a shared path have no quoting to lose.
    """
    src = os.path.join(snap_handoff, "producer")
    names = [n for n in ("derived.json", "derived.rank0.json", "derived.rank1.json")
             if os.path.exists(os.path.join(src, n))]
    if not names:
        return 0
    stage = os.path.join(outdir, "manifest")
    os.makedirs(stage, exist_ok=True)
    for n in names:
        shutil.copyfile(os.path.join(src, n), os.path.join(stage, n))
    dst = os.path.join(HANDOFF, tag)
    c = keepctl.CONTAINER[node]
    inner = f"mkdir -p {dst} && cp {stage}/*.json {dst}/ && ls {dst}"
    out = sh(node, f"docker exec {c} bash -lc {shlex.quote(inner)}", timeout=120)
    return {"staged": len(names), "target_dir": out.strip()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--snap", default="qwen36")
    ap.add_argument("--src", default="node27")
    ap.add_argument("--src-gpus", default="0,1")
    ap.add_argument("--dst", default="node29")
    ap.add_argument("--dst-gpus", default="0,1")
    ap.add_argument("--tag", default="restore_f1")
    ap.add_argument("--mem-fraction", default="0.70")
    ap.add_argument("--snap-handoff", default=os.path.join(
        ROOT, "codex_coding/results/v0.2.0/compare06_b2fix/handoff"))
    ap.add_argument("--local-stage", action="store_true")
    ap.add_argument("--answer-only", action="store_true", help="skip the target, just solve the LP")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    src_gpus = [int(x) for x in args.src_gpus.split(",")]
    dst_gpus = [int(x) for x in args.dst_gpus.split(",")]
    members = keepctl.parse_mesh()
    rep = {"arm": "F_restore_end_to_end", "model": MODEL, "snap": args.snap,
           "src": args.src, "dst": args.dst, "tag": args.tag, "handoff": HANDOFF}
    target_up = False
    try:
        # ---- 1. destination skeleton. Nothing can be written until this exists.
        t_skel = time.time()
        if not args.answer_only:
            stop_target(args.dst, args.out)
            time.sleep(2)
            rep["derived_staged"] = stage_derived(args.dst, args.snap_handoff, args.tag, args.out)
            rep["target_start"] = start_target(args.dst, args.out, args.tag, args.mem_fraction)
            rep["target_up"] = True
            target_up = True
        rep["skeleton_wall_s"] = time.time() - t_skel

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
            if args.local_stage:
                copies.append(Copy(kind="dram", node=args.dst, gpu=dgpu))
            demands.append(Demand(tp=tp, node=args.dst, gpu=dgpu,
                                  nbytes=max(o + n for o, n in index[tp].values()), copies=copies))
        T, split, load = solve(demands)
        wid = lambda n, g: members.index((n, g))
        ops = to_ops(demands, split, wid)
        route_of = [(i, route(demands[i].copies[j], demands[i])[0]) for i, j, _ in split]
        rep["plan"] = {"lp_T_s": T, "ops": len(ops),
                       "split_GB": [(i, j, round(b / GB, 3)) for i, j, b in split],
                       "route_of": route_of,
                       "load_GBps": {k: round(v, 2) for k, v in sorted(load.items())}}

        # ---- 3. flow into the live target's HBM.
        # Skipped on --answer-only: without a target that published a skeleton, `run_flow` blocks in
        # `wait_skeleton` for its full timeout. The plan above is the whole answer in that mode.
        if not args.answer_only:
            t_flow = time.time()
            body = {"ops": ops, "into": "hbm", "tag": args.tag, "snap": args.snap,
                    "retain_live": True,
                    "index": {str(tp): index[tp] for tp in index},
                    "meta": {str(tp): meta[tp] for tp in meta}}
            touched = {members[w] for o in ops for w in (o.get("src_w"), o["dst_w"])
                       if w is not None}
            res = keepctl.multi_call(members, "flow", body, only=touched, timeout=3600)
            rep["flow_wall_s"] = time.time() - t_flow
            rep["flow"] = {f"{k[0]}:{k[1]}": {kk: vv for kk, vv in v.items()
                                              if kk in ("total_s", "ib_s", "prep_s", "ib_bytes",
                                                        "h2d_bytes", "ssd_bytes", "local")}
                           for k, v in res.items() if isinstance(v, dict)}
            rep["errors"] = {f"{k[0]}:{k[1]}": v["error"][:400] for k, v in res.items()
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
            ref = json.load(open(os.path.join(args.snap_handoff, "..", "summary.json"))).get(
                "producer_replies")
            if ref:
                rep["output_matches_reference"] = [
                    a.get("text") == b.get("text") for a, b in zip(rep["replies"], ref)]
                rep["passed"] = bool(rep["output_matches_reference"]) and all(
                    rep["output_matches_reference"])
    finally:
        if target_up and not args.answer_only:
            pass     # the target is the thing being measured; leave it up for inspection
        with open(os.path.join(args.out, "summary.json"), "w") as f:
            json.dump(rep, f, indent=2)
        print(json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()
