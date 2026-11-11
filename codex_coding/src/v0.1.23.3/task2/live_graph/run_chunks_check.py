"""Chunk a real checkpoint, keep the chunks in DRAM, and prove transparent reassembly.

Usage: python run_chunks_check.py <model_dir> <result_dir> [chunk_MiB ...]
The source directory is only read. For each chunk size: build the manifest (one streaming pass,
chunks go straight into a MemChunkStore), then
  * reassemble every file from chunks and compare its sha256 with an independent hash of the
    original file;
  * compare every tensor's bytes from chunks with a direct read of the original file at the
    offset given by the safetensors header (parsed here, not by formats.py).
Writes manifest_<C>MiB.json and summary.json to <result_dir> (refuses to overwrite).
"""
import hashlib
import json
import os
import struct
import sys
import time
from collections import Counter

from fluidckpt.live.chunks import ChunkedModel, MemChunkStore, build_manifest, save_manifest

MiB = 1 << 20


def file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(16 * MiB), b""):
            h.update(block)
    return h.hexdigest()


def direct_tensors(path):
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
    return {k: (8 + n + v["data_offsets"][0], v["data_offsets"][1] - v["data_offsets"][0])
            for k, v in header.items() if k != "__metadata__"}


def main(model_dir, rdir, *sizes):
    os.makedirs(rdir, exist_ok=True)
    if os.path.exists(os.path.join(rdir, "summary.json")):
        raise SystemExit(f"refusing to overwrite {rdir}/summary.json")
    sizes = [int(s) for s in sizes] or [64, 16]
    t = time.time()
    originals = {n: file_sha(os.path.join(model_dir, n)) for n in sorted(os.listdir(model_dir))
                 if os.path.isfile(os.path.join(model_dir, n))}
    summary = {"model_dir": model_dir, "original_sha256": originals, "hash_originals_s": time.time() - t, "runs": {}}
    for mib in sizes:
        store = MemChunkStore()
        m = build_manifest(model_dir, mib * MiB, sink=lambda c, b: store.put(c.id, b))
        view = ChunkedModel(m, lambda cid: store)
        t = time.time()
        files_ok = {}
        for name in m.files:
            h = hashlib.sha256()
            for part in view.iter_file(name):
                h.update(part)
            files_ok[name] = h.hexdigest() == originals[name]
        reassemble_s = time.time() - t
        t, tensors_ok, checked = time.time(), True, 0
        for name in m.files:
            if not name.endswith(".safetensors"):
                continue
            with open(os.path.join(model_dir, name), "rb") as f:
                for tname, (off, n) in direct_tensors(os.path.join(model_dir, name)).items():
                    f.seek(off)
                    tensors_ok &= view.tensor_bytes(tname) == f.read(n)
                    checked += 1
        tensor_s = time.time() - t
        sizes_hist = Counter(min(c.nbytes // MiB, mib) for c in m.chunks)
        split = sum(1 for t in m.tensors.values() if len(t["spans"]) > 1)
        run = {"chunk_MiB": mib, "chunks": len(m.chunks), "unique_chunks": len(m.unique()),
               "stored_bytes": store.nbytes(), "source_bytes": sum(f["size"] for f in m.files.values()),
               "tensors": len(m.tensors), "tensors_split": split,
               "max_chunk_bytes": max(c.nbytes for c in m.chunks),
               "chunk_size_hist_MiB": dict(sorted(sizes_hist.items())),
               "build": m.build, "reassemble_s": reassemble_s, "tensor_check_s": tensor_s,
               "files_byte_identical": files_ok, "tensors_checked": checked, "tensors_identical": tensors_ok}
        summary["runs"][mib] = run
        save_manifest(m, os.path.join(rdir, f"manifest_{mib}MiB.json"))
        print(json.dumps(run))
        del store, view
    json.dump(summary, open(os.path.join(rdir, "summary.json"), "w"), indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:])
