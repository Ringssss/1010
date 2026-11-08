#!/usr/bin/env python3
"""Keep GLM-5.3 alive across node27/28/29 as a max-flow, then restore and time it.

One pass, no prompts:

  1. launch SGLang in capture mode on node27+node29 (real safetensors load, full post-processing)
  2. keepers capture every rank's post-processed shard into pinned DRAM as soon as it exports
  3. stage the derived-attr manifest to NFS (the restore nodes wipe /dev/shm before they start)
  4. save each node's shards to its own local SSD
  5. spread a second copy onto node28's keeper DRAM (the node with no rank of its own)
  6. drop node29's DRAM copy, so the restore must flow from {ssd, dram} together
  7. relaunch with --load-format dummy behind the restore hook, time to "fired up"

Step 7 is the number to compare against the vanilla baseline (see logs/<tag>_base_n0.t0).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(ROOT, "main_lib"))
sys.path.insert(0, os.path.join(ROOT, "codex_coding", "src", "serving"))
import glm_e2e as E  # noqa: E402
from fluidckpt.serving import ctl  # noqa: E402

M = E.M
A = argparse.ArgumentParser()
A.add_argument("--tag", default="keep2")
A.add_argument("--snap", default="glm53")
A.add_argument("--node0", default="node27")
A.add_argument("--node1", default="node29")
A.add_argument("--mid", default="node28")          # the third node; holds DRAM copies only
A.add_argument("--ssd", default="/data/ssd0/fluidckpt")
A.add_argument("--port", type=int, default=31753)
A.add_argument("--load-timeout", type=float, default=4200)
A.add_argument("--skip-capture", action="store_true", help="snapshot already in keeper DRAM")
A.add_argument("--resume", action="store_true",
               help="SGLang is already loading in capture mode: arm the keepers now and pick up "
                    "the load in progress instead of relaunching (~35 min saved)")
a = A.parse_args()

LOG = open(f"{ROOT}/logs/{a.tag}_pipeline.log", "w", buffering=1)
T0 = time.time()


def say(*xs):
    print(f"[{time.time() - T0:7.1f}s]", *xs, file=LOG)
    LOG.flush()


def phase(n, name):
    say(f"=== phase {n}: {name} ===")


# ------------------------------------------------------------------ 1+2 capture
if not a.skip_capture:
    phase(1, "launch SGLang (capture) + keeper capture")
    subprocess.call(["bash", f"{ROOT}/scripts/glm_stop.sh", a.node0, a.node1, a.mid])

    captured = {}

    def arm():
        try:
            only = {m for m in M if m[0] in (a.node0, a.node1)}
            r = ctl.all_call(M, "capture", {"tag": a.tag, "snap": a.snap}, only=only, timeout=7200)
            rows = [x for v in r.values() for x in v]
            if not rows:
                # Never let an empty result reach the summary math (it used to die on sorted([])[0]
                # and take the whole 35-minute run down with it).
                failed = {f"{k[0]}:{k[1]}": v for k, v in r.items() if not v}
                raise RuntimeError(f"keeper capture returned nothing for {len(failed)} workers: "
                                   f"{json.dumps(failed)[:400]}")
            captured["rows"] = rows
            say(f"keeper captured {len(rows)} ranks, "
                f"{sum(x['GiB'] for x in rows):.1f} GiB, "
                f"d2h median {sorted(x['d2h_GBps'] for x in rows)[len(rows)//2]:.2f} GB/s")
        except Exception as e:  # noqa: BLE001
            captured["err"] = repr(e)
            say("capture FAILED:", repr(e))

    th = threading.Thread(target=arm, daemon=True)

    if a.resume:
        # The keepers are already armed above; SGLang was launched by an earlier attempt and is
        # still loading. Nothing to start, and T0 must stay the *original* launch time or the
        # baseline comparison would be measured against the wrong instant.
        t0 = float(open(f"{ROOT}/logs/{a.tag}_n0.t0").read().strip()) if \
            os.path.exists(f"{ROOT}/logs/{a.tag}_n0.t0") else time.time()
        th.start()
        say(f"resuming: SGLang already loading (t0={time.strftime('%H:%M:%S', time.localtime(t0))}), "
            f"{time.time() - t0:.0f}s elapsed")
    else:
        th.start()
        time.sleep(3)   # let the keepers arm before SGLang exports

        t0 = time.time()
        subprocess.check_call(["bash", f"{ROOT}/scripts/glm_serve.sh", a.tag, a.node0, a.node1],
                              env=dict(os.environ, FLUIDCKPT_MODE="capture", PORT=str(a.port)),
                              cwd=ROOT)
        say(f"launched; load starts. waiting for 'fired up' in logs/{a.tag}_n0.log")

    log = f"{ROOT}/logs/{a.tag}_n0.log"
    while time.time() - t0 < a.load_timeout:
        try:
            s = open(log, errors="ignore").read()
        except OSError:
            s = ""
        if "fired up" in s:
            break
        if "RuntimeError" in s or "Traceback" in s:
            say("!! load raised, see", log)
            say(s[-3000:])
            sys.exit(1)
        time.sleep(2)
    else:
        say("!! load timed out")
        sys.exit(1)
    t_load = time.time() - t0
    say(f"SGLang ready (vanilla load path) in {t_load:.1f}s")

    th.join(timeout=1800)
    if "err" in captured:
        sys.exit(1)
    rows = captured["rows"]
    json.dump({"t_load_s": t_load, "capture": rows},
              open(f"{ROOT}/benchmarks/results/{a.tag}_capture.json", "w"), indent=1)

    phase(3, "stage derived-attr manifest to NFS")
    man = E.stage_manifest(a.tag, a.node0)
    say("manifest:", man)

# ------------------------------------------------------------------ 4 save
phase(4, "save keeper DRAM -> local SSD on each node")
for node in (a.node0, a.node1):
    r = ctl.all_call(M, "save", {"snap": a.snap, "dir": f"{a.ssd}/{a.snap}"},
                     only={m for m in M if m[0] == node})
    rows = [x for v in r.values() for x in v]
    if rows:
        say(f"  {node}: {len(rows)} ranks, {sum(x['GiB'] for x in rows):.1f} GiB, "
            f"max {max(x['s'] for x in rows):.1f}s")

# ------------------------------------------------------------------ 5 spread to node28
phase(5, f"spread a second copy onto {a.mid} DRAM (max-flow over IB)")
E.cmd_spread(argparse.Namespace(snap=a.snap, to=a.mid, tps=""))

# ------------------------------------------------------------------ 6 drop node29 DRAM
phase(6, f"drop {a.node1} DRAM copies (force the restore to mix ssd+dram)")
E.cmd_drop(argparse.Namespace(snap=a.snap, node=a.node1))

# ------------------------------------------------------------------ 7 restore + time it
phase(7, "restore: dummy skeleton + max-flow fill, time to ready")
r = E.cmd_restore(argparse.Namespace(
    snap=a.snap, tag=a.tag, node0=a.node0, node1=a.node1,
    ssd=[f"{a.mid}:{a.ssd}/{a.snap}", f"{a.node0}:{a.ssd}/{a.snap}", f"{a.node1}:{a.ssd}/{a.snap}"],
    only_kinds="", port=a.port, timeout=a.load_timeout))

say("=== done ===")
print(json.dumps(r, indent=1))
