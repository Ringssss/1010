"""Audit the ingest plan for one checkpoint: split axis, read-call count, byte weight.

Read calls are the cost model that matters -- a dim-0 split is one contiguous read, a dim-1
(column-parallel) split is one read per outer row. If most bytes sit on strided tensors, the
packed shard is read-bound no matter how fast the link is.

  python3 ingest_audit.py /data/node28/shared/zhujianian/Qwen3.6-35B-A3B --tp 0 --tp-size 2
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, "/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib")
from fluidckpt.model import ingest  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model_dir")
    ap.add_argument("--tp", type=int, default=0)
    ap.add_argument("--tp-size", type=int, default=2)
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()

    idx_path = os.path.join(a.model_dir, "model.safetensors.index.json")
    wm = json.load(open(idx_path))["weight_map"] if os.path.exists(idx_path) else None
    files = sorted(f for f in os.listdir(a.model_dir) if f.endswith(".safetensors"))

    stats = collections.Counter()
    nreads = collections.Counter()
    nbytes = collections.Counter()
    worst = []
    for fname in files:
        heads, _ = ingest._header(os.path.join(a.model_dir, fname))
        for name, (dt, shape, begin, end) in sorted(heads.items()):
            if wm is not None and wm.get(name) != fname:
                continue
            ax = ingest.split_axis(name, shape)
            parts = ingest.rangefor(ax, shape, a.tp, a.tp_size, dt) if ax is not None \
                else [(None, 0, end - begin)]
            key = "replicated" if ax is None else f"axis{ax}"
            stats[key] += 1
            nreads[key] += len(parts)
            nbytes[key] += sum(p[2] for p in parts)
            if ax is not None and ax != 0:
                worst.append((len(parts), sum(p[2] for p in parts), name, tuple(shape)))

    worst.sort(reverse=True)
    total_reads = sum(nreads.values())
    out = {
        "model_dir": a.model_dir, "tp_rank": a.tp, "tp_size": a.tp_size,
        "timing": {"reads": "call these; size = the cost model"},
        "tensors_by_split": dict(stats),
        "read_calls_by_split": dict(nreads),
        "GiB_by_split": {k: round(v / 2**30, 3) for k, v in nbytes.items()},
        "total_read_calls": total_reads,
        "total_GiB": round(sum(nbytes.values()) / 2**30, 3),
        "median_read_bytes": round(sum(nbytes.values()) / max(total_reads, 1)),
        "strided_share_of_reads": round(nreads.get("axis1", 0) / max(total_reads, 1), 4),
        "strided_share_of_bytes": round(nbytes.get("axis1", 0) / max(sum(nbytes.values()), 1), 4),
    }
    print(json.dumps(out, indent=1))
    print(f"\ntop {a.top} strided tensors (nreads, MiB, shape, name):")
    for n, b, name, shape in worst[:a.top]:
        print(f"  {n:>7} reads  {b / 2**20:>9.1f} MiB  {shape}  {name}")


if __name__ == "__main__":
    main()
