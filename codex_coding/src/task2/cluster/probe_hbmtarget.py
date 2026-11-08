"""Reproduce HbmTarget construction on the destination node, outside the keeper.

The keeper answered the flow request with an exception, but the reply never reached the driver
(a later BrokenPipe overwrote it), so the cause is invisible from the driver side. Rebuilding the
target here with the same pickle, index and meta puts the real traceback on stdout.
"""
import json, pickle, sys, traceback
sys.path.insert(0, "/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib")
from fluidckpt.serving.keeper import HbmTarget

tp = int(sys.argv[1])
gpu = int(sys.argv[2])
d = json.load(open(f"/tmp/probe_idx{tp}.json"))
idx = {k: tuple(v) for k, v in d["index"].items()}
print(f"index entries={len(idx)} meta_keys={sorted(d['meta'].keys())}", flush=True)
print(f"meta identity={d['meta'].get('model_identity')!r} tp_size={d['meta'].get('tp_size')!r} "
      f"tp_rank={d['meta'].get('tp_rank')!r} n={len(d['meta'].get('tensors', []))}", flush=True)
print("building HbmTarget...", flush=True)
try:
    t = HbmTarget(f"/dev/shm/fluidckpt/restore_f3/rank{tp}.pkl", idx, gpu, d["meta"])
    print(f"OK segs={len(t.segs)} missing={len(t.missing)}", flush=True)
except BaseException:
    traceback.print_exc()
    sys.exit(1)
