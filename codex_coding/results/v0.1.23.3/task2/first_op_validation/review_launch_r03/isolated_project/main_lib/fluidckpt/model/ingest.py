"""Ingest a safetensors checkpoint into one packed, TP-partitioned shard.

This is the expensive work the keepalive exists to avoid repeating: read the weights off
storage, partition every tensor for this TP rank, transform to the served layout, and pack
the result into one contiguous buffer.

The partition is defined here once so every path shares it. A cold reload from shared storage
and a warm flow out of pinned DRAM must produce byte-identical shards, or the comparison
measures two different jobs.

Safetensors stores a JSON header of {name: {dtype, shape, data_offsets}} after an 8-byte
little-endian length. Reading that header directly means a rank only touches the bytes it
actually owns -- a TP2 rank of a 67 GiB model reads ~33 GiB, not all of it.
"""
from __future__ import annotations

import json
import os
import struct

DTYPE_SIZE = {"F64": 8, "F32": 4, "F16": 2, "BF16": 2, "I64": 8, "I32": 4, "I16": 2,
              "I8": 1, "U8": 1, "BOOL": 1, "F8_E4M3": 1, "F8_E5M2": 1}

# Where a tensor is sharded for tensor parallelism, and on which axis. Longest match wins, so
# `gate_proj` is tested before the bare `gate` used by some MoE naming schemes.
SPLIT_RULES = (
    ("q_proj", 0), ("k_proj", 0), ("v_proj", 0), ("o_proj", 1),
    ("q_a_proj", 0), ("kv_a_proj", 0), ("q_b_proj", 0), ("kv_b_proj", 1),
    ("gate_proj", 0), ("up_proj", 0), ("down_proj", 1),
    ("gate_up_proj", 0),
    ("w1", 0), ("w3", 0), ("w2", 1),
    ("linear_attn.in_proj", 0), ("linear_attn.out_proj", 1),
    ("embed_tokens", 0), ("lm_head", 0),
    ("experts", 0),
)
# Replicated across ranks no matter how the name looks: norms, biases, scalars.
REPLICATE = ("norm", "layernorm", "ln_", "bias", "A_log", "dt_bias", "e_score_correction_bias",
             "rotary", "inv_freq")


def _header(path):
    """(tensor_name -> (dtype, shape, begin, end)), data_start_byte)."""
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        meta = json.loads(f.read(n))
    base = 8 + n
    out = {}
    for name, info in meta.items():
        if name == "__metadata__":
            continue
        b, e = info["data_offsets"]
        out[name] = (info["dtype"], info["shape"], base + b, base + e)
    return out, base


def split_axis(name, shape):
    """Axis this tensor is partitioned on for TP, or None when it is replicated.

    Same shape on every rank is the invariant for replicated tensors; a 1-D tensor is never
    split (norm weights, biases, per-head scalars).
    """
    low = name.lower()
    if len(shape) < 2:
        return None
    for token in REPLICATE:
        if token in low:
            return None
    best = None
    for token, axis in SPLIT_RULES:
        if token in low and (best is None or len(token) > len(best[0])):
            best = (token, axis)
    if best is None:
        return None
    axis = best[1]
    # Never split an axis that does not divide evenly unless it is the last resort; a rule that
    # names a non-existent axis (e.g. dim 1 of a 2-D tensor stored transposed) is not applicable.
    return axis if axis < len(shape) else None


def rangefor(axis, shape, tp_rank, tp_size, dtype):
    """Byte ranges within a tensor this rank owns: [(row_index, byte_offset_in_tensor, length)].

    A dim-0 split is one contiguous range. Any other axis is strided, so each outer row is a
    separate read -- that is the real cost of a column-parallel partition and is measured as such.
    """
    item = DTYPE_SIZE[dtype]
    if axis is None:
        n = item
        for d in shape:
            n *= d
        return [(None, 0, n)]
    if shape[axis] % tp_size:
        raise ValueError(f"axis {axis} of {shape} does not divide by tp_size {tp_size}")
    outer = 1
    for d in shape[:axis]:
        outer *= d
    inner = item
    for d in shape[axis + 1:]:
        inner *= d
    axis_len = shape[axis]
    lo = axis_len * tp_rank // tp_size
    hi = axis_len * (tp_rank + 1) // tp_size
    stride = axis_len * inner
    return [(o, o * stride + lo * inner, (hi - lo) * inner) for o in range(outer)]


def plan(model_dir, tp_rank, tp_size, index_path=None):
    """Every byte this rank needs, grouped by source file.

    Returns {file: [(tensor, dest_offset_in_shard, file_offset, length)]}, total_bytes, and the
    shard index {tensor: (dest_offset, nbytes)}. Grouped by file so each file is opened once.
    """
    index_path = index_path or os.path.join(model_dir, "model.safetensors.index.json")
    if os.path.exists(index_path):
        weight_map = json.load(open(index_path))["weight_map"]
        files = sorted(set(weight_map.values()))
    else:  # single-file checkpoint
        files = sorted(f for f in os.listdir(model_dir) if f.endswith(".safetensors"))
        weight_map = None

    by_file, index, dest = {}, {}, 0
    for fname in files:
        path = os.path.join(model_dir, fname)
        heads, _ = _header(path)
        for name, (dtype, shape, begin, end) in sorted(heads.items()):
            if weight_map is not None and weight_map.get(name) != fname:
                continue
            axis = split_axis(name, shape)
            total = end - begin
            parts = rangefor(axis, shape, tp_rank, tp_size, dtype) if axis is not None else \
                [(None, 0, total)]
            nbytes = sum(p[2] for p in parts)
            index[name] = (dest, nbytes)
            for _row, off, length in parts:
                by_file.setdefault(fname, []).append((name, dest, begin + off, length))
                dest += length
    return by_file, dest, index


def ingest(model_dir, tp_rank, tp_size, out_path, index_path=None, progress=None):
    """Write the packed shard for `tp_rank` to `out_path`. Returns (bytes, index)."""
    by_file, total, index = plan(model_dir, tp_rank, tp_size, index_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    written = 0
    with open(out_path, "wb", buffering=0) as out, open(out_path + ".idx", "w") as jf:
        for fname in sorted(by_file):
            with open(os.path.join(model_dir, fname), "rb", buffering=0) as src:
                for _name, dest, off, length in sorted(by_file[fname], key=lambda r: r[1]):
                    src.seek(off)
                    buf = src.read(length)
                    if len(buf) != length:
                        raise IOError(f"short read {fname}@{off}+{length}")
                    out.seek(dest)
                    out.write(buf)
                    written += length
            if progress:
                progress(fname, written, total)
        json.dump({"index": index, "total_bytes": total,
                   "model_dir": model_dir, "tp_rank": tp_rank, "tp_size": tp_size}, jf)
    return written, index


def _coalesce(reads, max_gap, max_span):
    """Group reads that are adjacent in the file into fewer, larger spans.

    A column-parallel tensor is one read per outer row: `o_proj` at TP2 is 2048 reads of 2 KiB,
    which costs far more in syscalls and slice dispatch than in bytes -- measured at 1.36 GB/s
    with sys time exceeding user time. Reading the span that covers several segments at once and
    slicing them out in memory trades a little over-read for far fewer syscalls.

    Two independent bounds, and both are load-bearing. `max_gap` stops a span from reaching
    across unrelated weights. `max_span` stops the span itself from growing without limit: a
    tensor's rows are nearly contiguous, so with only a gap bound 4697 segments merge into one
    2 GiB span that runs past the end of the file.

    `reads` is [(name, dest, file_offset, length)] emitted in ascending dest, which plan()
    guarantees is also ascending file offset. Grouping only changes how bytes come off storage,
    never where they land in the packed shard.

    A single contiguous range can exceed `max_span` on its own -- a 512 MiB expert block is one
    read no matter what the cap says -- so `max_span` only gates whether the *next* range may
    join the current span. The span's start is the minimum file offset among its pieces, not the
    first one appended: pieces are collected in iteration order, which is not file order.

    Returns [(span_offset, span_length, [(dest, offset_in_span, length)])].
    """
    if not reads:
        return []
    order = sorted(reads, key=lambda r: r[2])
    spans = []
    start = order[0][2]
    end = order[0][2] + order[0][3]
    pieces = [(order[0][1], order[0][2] - start, order[0][3])]
    for _name, dest, off, length in order[1:]:
        # `gap` is against the current span's END; comparing an offset to the span start instead
        # is a unit mix-up that silently produces spans running past EOF.
        gap = off - end
        fits = 0 <= gap <= max_gap and (off + length) - start <= max_span
        if fits:
            pieces.append((dest, off - start, length))
            end = max(end, off + length)
        else:
            spans.append((start, end - start, pieces))
            start, end = off, off + length
            pieces = [(dest, 0, length)]
    spans.append((start, end - start, pieces))
    return spans


def fill(model_dir, tp_rank, tp_size, dest_buf, index_path=None, progress=None,
         coalesce=0, max_span=32 << 20):
    """Partition-and-pack straight into `dest_buf` (a flat byte buffer), skipping any file.

    This is the path a serving node actually takes on a cold start: there is no intermediate
    packed file to write and re-read, the bytes land where the loader needs them. `dest_buf`
    supports slice assignment from a bytes/bytearray, which both a numpy view over pinned
    memory and a mmap do.

    Coalescing is OFF by default, against first intuition. Measured on Qwen3.6 TP2, 33.9 GiB per
    rank: reading each range separately gave 14.31 s / 2.55 GB/s, while bridging gaps up to 1 MiB
    into 865 large reads gave 24.44 s / 1.49 GB/s -- 1.7x SLOWER. The 179354 small reads are not
    syscall-bound once pages are cached; what coalescing adds is over-read, because most gaps
    inside a checkpoint are padding that gets pulled in for nothing. `coalesce` is the largest
    file-offset gap bridged into one read, `max_span` caps how large a bridged read may grow;
    set `coalesce` nonzero only when the source is cold and seek-dominated.
    """
    by_file, total, index = plan(model_dir, tp_rank, tp_size, index_path)
    if len(dest_buf) < total:
        raise ValueError(f"destination holds {len(dest_buf)} bytes, shard needs {total}")
    written = 0
    for fname in sorted(by_file):
        with open(os.path.join(model_dir, fname), "rb", buffering=0) as src:
            reads = by_file[fname]
            if coalesce and max_span:
                for span_off, span_len, pieces in _coalesce(reads, coalesce, max_span):
                    src.seek(span_off)
                    span = src.read(span_len)
                    if len(span) != span_len:
                        raise IOError(f"short read {fname}@{span_off}+{span_len}")
                    if len(pieces) == 1 and pieces[0][1] == 0 and pieces[0][2] == span_len:
                        dest_buf[pieces[0][0]:pieces[0][0] + span_len] = span
                        written += span_len
                        continue
                    mv = memoryview(span)
                    for dest, off_in_span, length in pieces:
                        dest_buf[dest:dest + length] = mv[off_in_span:off_in_span + length]
                        written += length
            else:
                src.seek(reads[0][2])
                for _name, dest, off, length in sorted(reads, key=lambda r: r[2]):
                    if src.tell() != off:
                        src.seek(off)
                    buf = src.read(length)
                    if len(buf) != length:
                        raise IOError(f"short read {fname}@{off}+{length}")
                    dest_buf[dest:dest + length] = buf
                    written += length
        if progress:
            progress(fname, written, total)
    if written != total:
        raise IOError(f"packed {written} of {total} bytes")
    return written, index


if __name__ == "__main__":
    import argparse
    import time

    ap = argparse.ArgumentParser()
    ap.add_argument("model_dir")
    ap.add_argument("--tp-rank", type=int, default=0)
    ap.add_argument("--tp-size", type=int, default=2)
    ap.add_argument("--out", default=None)
    ap.add_argument("--plan-only", action="store_true")
    a = ap.parse_args()

    if a.plan_only:
        by_file, total, index = plan(a.model_dir, a.tp_rank, a.tp_size)
        per_file = {f: sum(r[3] for r in rs) for f, rs in by_file.items()}
        reads = sum(len(rs) for rs in by_file.values())
        print(json.dumps({"total_GiB": total / 2**30, "tensors": len(index),
                          "read_calls": reads, "files": len(by_file),
                          "per_file_GiB": {k: round(v / 2**30, 3) for k, v in per_file.items()}},
                         indent=1))
    else:
        out = a.out or f"/tmp/shard-r{a.tp_rank}.bin"
        t0 = time.time()
        n, _ = ingest(a.model_dir, a.tp_rank, a.tp_size, out,
                      progress=lambda f, w, t: print(f"  {f} {w/2**30:.1f}/{t/2**30:.1f} GiB", flush=True))
        s = time.time() - t0
        print(json.dumps({"out": out, "GiB": n / 2**30, "s": round(s, 2),
                          "GBps": round(n / s / 1e9, 2)}))
