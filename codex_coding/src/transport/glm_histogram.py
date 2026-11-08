"""Extract GLM-5.3's real per-tensor size histogram from safetensors headers only.

Reads just the 8-byte header length + JSON header of each shard (1.3 GiB files, but we touch
~1 MiB of each), so it runs in seconds. Output is the input to the scheduling model: the shape
of the transfer problem is determined by 118,629 tensor sizes, not by total bytes.
"""
import json
import os
import struct
import sys
from collections import Counter


def header_sizes(path):
    with open(path, "rb") as f:
        (n,) = struct.unpack("<Q", f.read(8))
        hdr = json.loads(f.read(n))
    out = {}
    for name, meta in hdr.items():
        if name == "__metadata__":
            continue
        off = meta["data_offsets"]
        out[name] = off[1] - off[0]
    return out


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else "/data/node28/shared/zhujianian/GLM-5.3"
    files = sorted(f for f in os.listdir(d) if f.endswith(".safetensors"))
    all_sizes = {}
    for i, fn in enumerate(files):
        all_sizes.update(header_sizes(os.path.join(d, fn)))
        if (i + 1) % 40 == 0:
            print(f"  {i+1}/{len(files)} shards", file=sys.stderr, flush=True)
    sizes = sorted(all_sizes.values())
    total = sum(sizes)
    n = len(sizes)
    print(f"tensors={n} total={total/2**30:.1f} GiB mean={total/n/1024:.1f} KiB median={sizes[n//2]/1024:.1f} KiB")
    # log2 buckets
    hist = Counter()
    for s in sizes:
        b = 0
        while (1 << (b + 1)) <= max(s, 1):
            b += 1
        hist[b] += 1
    print(f"{'bucket':>14} {'count':>8} {'bytes':>14} {'%count':>7} {'%bytes':>7}")
    for b in sorted(hist):
        lo, hi = 1 << b, (1 << (b + 1)) - 1
        cnt = hist[b]
        byt = sum(s for s in sizes if lo <= s <= hi)
        print(f"{lo/1024:6.0f}-{hi/1024:6.0f} KiB {cnt:8d} {byt/2**20:11.1f} MiB "
              f"{cnt/n*100:6.1f}% {byt/total*100:6.1f}%")
    # what bucketing to 32/64/128 MiB would give
    for target in (16, 32, 64, 128):
        tgt = target * 2 ** 20
        nb = 0
        acc = 0
        for s in sizes:
            if acc + s > tgt and acc > 0:
                nb += 1
                acc = 0
            acc += s
        if acc:
            nb += 1
        print(f"  bucket {target:4d} MiB -> {nb:7d} broadcasts  ({nb/n*100:.2f}% of per-tensor count)")
    json.dump({k: v for k, v in all_sizes.items()},
              open("/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/model/glm53_tensor_sizes.json", "w"))
    print("wrote benchmarks/results/glm53_tensor_sizes.json")


if __name__ == "__main__":
    main()
