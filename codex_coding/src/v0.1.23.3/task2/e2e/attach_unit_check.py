"""CPU-only check of the attach descriptor math (no GPU, no daemons).

Builds a small safetensors checkpoint, its FluidPool manifest, places the files like Pool.layout
does into one CPU byte buffer per "card", and checks that sglang_attach.iter_tensors yields every
tensor bit-exactly.   attach_unit_check.py <tmp_dir>"""
import os
import sys
import types

import torch
from safetensors.torch import load_file, save_file

from fluidckpt.live.chunks import build_manifest
from fluidckpt.live.pool import Pool
from fluidckpt.serve import sglang_attach as SA

d = sys.argv[1]
os.makedirs(d, exist_ok=True)
g = torch.Generator().manual_seed(0)
a = {f"model.layers.{i}.w": torch.randn(64, 48 + i, generator=g).to(torch.bfloat16) for i in range(6)}
b = {"lm_head.weight": torch.randn(100, 64, generator=g), "model.norm.weight": torch.ones(64, dtype=torch.float16),
     "ids": torch.arange(10, dtype=torch.int64)}
save_file(a, os.path.join(d, "model-00001-of-00002.safetensors"))
save_file(b, os.path.join(d, "model-00002-of-00002.safetensors"))
man = build_manifest(d, chunk_bytes=8192)
model = types.SimpleNamespace(manifest=man, chunks=man.chunks)
for cards in ([0], [0, 1]):
    base, used = Pool.layout(None, model, cards)
    bufs = {c: torch.zeros(max(used[c], 1), dtype=torch.uint8) for c in cards}
    for f, (c, o) in base.items():
        raw = open(os.path.join(d, f), "rb").read()
        bufs[c][o:o + len(raw)] = torch.frombuffer(bytearray(raw), dtype=torch.uint8)
    desc = {"layout": {f: [c, o] for f, (c, o) in base.items()},
            "tensors": [[t, v["file"], v["offset"], v["nbytes"], v["dtype"], v["shape"]] for t, v in man.tensors.items()]}
    got = dict(SA.iter_tensors(desc, bufs))
    want = {**load_file(os.path.join(d, "model-00001-of-00002.safetensors")),
            **load_file(os.path.join(d, "model-00002-of-00002.safetensors"))}
    assert set(got) == set(want), (set(got) ^ set(want))
    for k in want:
        assert got[k].dtype == want[k].dtype and tuple(got[k].shape) == tuple(want[k].shape), k
        assert torch.equal(got[k].view(torch.uint8) if got[k].dim() else got[k], want[k].view(torch.uint8) if want[k].dim() else want[k]), k
    print(f"cards={cards}: {len(got)} tensors bit-exact, layout={desc['layout']}")
print("OK")
