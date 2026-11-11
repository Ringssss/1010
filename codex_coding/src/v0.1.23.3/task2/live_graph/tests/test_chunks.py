"""Tests for live/formats.py and live/chunks.py with self-written safetensors and GGUF files."""
import hashlib
import json
import os
import random
import struct

import pytest

from fluidckpt.live.chunks import (ChunkedModel, DirChunkStore, MemChunkStore, build_manifest, cut_points,
                                   load_manifest, save_manifest)
from fluidckpt.live.formats import describe, gguf_table, safetensors_table

KiB = 1024


def write_safetensors(path, tensors, metadata=None):
    """tensors: [(name, dtype, shape, bytes)] laid out in order."""
    header, off = {}, 0
    if metadata:
        header["__metadata__"] = metadata
    for name, dtype, shape, data in tensors:
        header[name] = {"dtype": dtype, "shape": shape, "data_offsets": [off, off + len(data)]}
        off += len(data)
    h = json.dumps(header).encode()
    h += b" " * (-len(h) % 8)
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(h)) + h)
        for *_, data in tensors:
            f.write(data)


def gguf_str(s):
    b = s.encode()
    return struct.pack("<Q", len(b)) + b


def write_gguf(path, tensors, align=32):
    """tensors: [(name, dims, ggml_type, bytes)]. Metadata includes arrays to exercise the parser."""
    kv = [("general.architecture", 8, gguf_str("llama")),
          ("general.alignment", 4, struct.pack("<I", align)),
          ("tokenizer.ggml.scores", 9, struct.pack("<IQ", 6, 3) + struct.pack("<3f", 0.1, 0.2, 0.3)),
          ("tokenizer.ggml.tokens", 9, struct.pack("<IQ", 8, 2) + gguf_str("a") + gguf_str("bc"))]
    out = b"GGUF" + struct.pack("<IQQ", 3, len(tensors), len(kv))
    for k, t, v in kv:
        out += gguf_str(k) + struct.pack("<I", t) + v
    rel, blobs = 0, []
    for name, dims, gtype, data in tensors:
        out += gguf_str(name) + struct.pack("<I", len(dims)) + b"".join(struct.pack("<Q", d) for d in dims)
        out += struct.pack("<IQ", gtype, rel)
        pad = -len(data) % align
        blobs.append(data + b"\0" * pad)
        rel += len(data) + pad
    out += b"\0" * (-len(out) % align)
    with open(path, "wb") as f:
        f.write(out + b"".join(blobs))


def rand(n, seed):
    return random.Random(seed).randbytes(n)


@pytest.fixture
def st_model(tmp_path):
    d = tmp_path / "st"
    d.mkdir()
    a = [("w.small0", "F32", [16], rand(64, 1)), ("w.big", "BF16", [3, 40 * KiB], rand(240 * KiB, 2)),
         ("w.small1", "F16", [8, 8], rand(128, 3)), ("w.empty", "F32", [0], b"")]
    b = [("x.mid", "I8", [100 * KiB], rand(100 * KiB, 4)), ("x.tail", "U8", [5], rand(5, 5))]
    write_safetensors(d / "model-00001-of-00002.safetensors", a, {"format": "pt"})
    write_safetensors(d / "model-00002-of-00002.safetensors", b)
    wm = {n: "model-00001-of-00002.safetensors" for n, *_ in a}
    wm.update({n: "model-00002-of-00002.safetensors" for n, *_ in b})
    (d / "model.safetensors.index.json").write_text(json.dumps({"weight_map": wm}))
    (d / "config.json").write_text('{"hidden": 16}')
    return d, {n: data for n, _, _, data in a + b}


def test_cut_points_rules():
    tensors = [{"offset": 100, "nbytes": 30}, {"offset": 130, "nbytes": 30}, {"offset": 160, "nbytes": 250}]
    cuts = cut_points(420, 100, tensors, 100)
    assert cuts[0] == 0 and cuts[1] == 100 and cuts[-1] == 420
    assert 160 in cuts                                       # big tensor starts a chunk
    assert all(b - a <= 100 for a, b in zip(cuts, cuts[1:]))
    assert cuts == sorted(set(cuts))


def test_safetensors_table(st_model):
    d, data = st_model
    t = safetensors_table(str(d / "model-00001-of-00002.safetensors"))
    names = [x["name"] for x in t["tensors"]]
    assert "__metadata__" not in names and t["metadata"] == {"format": "pt"}
    with open(d / "model-00001-of-00002.safetensors", "rb") as f:
        for x in t["tensors"]:
            f.seek(x["offset"])
            assert f.read(x["nbytes"]) == data[x["name"]]


@pytest.mark.parametrize("chunk", [1 * KiB, 64 * KiB, 1 << 20])
def test_roundtrip_safetensors(st_model, tmp_path, chunk):
    d, data = st_model
    store = MemChunkStore()
    m = build_manifest(str(d), chunk, sink=lambda c, b: store.put(c.id, b))
    m.validate()
    assert set(m.files) == {"model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors",
                            "config.json", "model.safetensors.index.json"}
    assert all(c.nbytes <= chunk for c in m.chunks)
    view = ChunkedModel(m, lambda cid: store)
    assert view.verify()
    for name, raw in data.items():
        assert view.tensor_bytes(name) == raw
    out = tmp_path / f"out{chunk}"
    view.materialize_dir(str(out))
    for name in m.files:
        assert (out / name).read_bytes() == (d / name).read_bytes()
    f = "model-00001-of-00002.safetensors"
    src = (d / f).read_bytes()
    for off, n in [(0, 8), (5, 3000), (len(src) - 7, 7), (1000, len(src) - 1000)]:
        assert view.read(f, off, n) == src[off:off + n]


def test_big_tensor_split_and_small_packed(st_model):
    d, _ = st_model
    m = build_manifest(str(d), 64 * KiB)
    assert len(m.tensors["w.big"]["spans"]) >= 4                    # 240 KiB over 64 KiB chunks
    assert len(m.tensors["w.small1"]["spans"]) == 1
    assert m.tensors["w.empty"]["spans"] == []


def test_gguf_roundtrip(tmp_path):
    d = tmp_path / "gg"
    d.mkdir()
    q8 = rand(34 * 4, 7)                                             # Q8_0: 4 blocks of 32 elements
    tensors = [("tok_embd.weight", [32, 4], 8, q8), ("norm.weight", [7], 0, rand(28, 8)),
               ("out.weight", [256, 2], 12, rand(144 * 2, 9))]       # Q4_K: 2 blocks of 256
    write_gguf(d / "m.gguf", tensors, align=64)
    t = gguf_table(str(d / "m.gguf"))
    assert t["metadata"]["general.alignment"] == 64 and t["header"][1] % 64 == 0
    assert [x["nbytes"] for x in t["tensors"]] == [len(x[3]) for x in tensors]
    store = MemChunkStore()
    m = build_manifest(str(d), 256, sink=lambda c, b: store.put(c.id, b))
    view = ChunkedModel(m, lambda cid: store)
    view.verify()
    assert m.files["m.gguf"]["format"] == "gguf"
    for name, _, _, raw in tensors:
        assert view.tensor_bytes(name) == raw
    assert describe(str(d / "m.gguf"))["format"] == "gguf"


def test_dedupe_and_dir_store(tmp_path):
    d = tmp_path / "dup"
    d.mkdir()
    block = rand(4 * KiB, 11)
    write_safetensors(d / "model.safetensors", [(f"t{i}", "U8", [4 * KiB], block) for i in range(6)])
    store = DirChunkStore(str(tmp_path / "ssd"))
    m = build_manifest(str(d), 4 * KiB, sink=lambda c, b: store.put(c.id, b))
    data_chunks = [c for c in m.chunks if c.file == "model.safetensors" and c.offset >= m.files["model.safetensors"]["header"][1]]
    assert len({c.id for c in data_chunks}) == 1 and len(data_chunks) == 6
    ChunkedModel(m, lambda cid: store).verify()
    path = tmp_path / "m.json"
    save_manifest(m, str(path))
    m2 = load_manifest(str(path))
    assert m2.chunks == m.chunks and m2.tensors == m.tensors
    ChunkedModel(m2, lambda cid: store).verify()


def test_corruption_detected(st_model):
    d, _ = st_model
    store = MemChunkStore()
    m = build_manifest(str(d), 64 * KiB, sink=lambda c, b: store.put(c.id, b))
    cid = m.tensors["w.big"]["spans"][0][0]
    cid = m.chunks[cid].id
    bad = bytearray(store.data[cid])
    bad[0] ^= 0xFF
    store.data[cid] = bytes(bad)
    with pytest.raises(IOError):
        ChunkedModel(m, lambda c: store).verify()
