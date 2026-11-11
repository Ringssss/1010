"""Chunked, content-addressed weight store that keeps the user's file format intact.

A model directory (safetensors shards, GGUF, plus config/tokenizer files) is cut into chunks:
byte ranges of the original files, identified by the sha256 of their content. Chunks are the unit
of placement: they can sit in any HBM/DRAM/SSD vertex of the live graph, fragmented across nodes.
Reassembly is a pure byte concatenation, so the user (or SGLang's loader) still sees the original
safetensors/GGUF bytes and nothing downstream needs to know chunks exist.

Cutting rule (per file, `chunk_bytes` target C):
  * the format header is its own chunk (small; it is what a loader reads first);
  * whole tensors are packed into the current chunk while it stays <= C; padding between
    tensors stays with the preceding chunk;
  * only a tensor larger than C is split, in C-sized pieces;
  * files without tensor structure (config, tokenizer, unknown) are split every C bytes.
Every byte of every file is covered exactly once (checked by Manifest.validate).

The manifest maps file -> chunks and tensor -> [(chunk index, offset in chunk, length)], so a
tensor can also be materialized directly from wherever its chunks live, without rebuilding files.
"""
import bisect
import hashlib
import json
import os
import time
from dataclasses import dataclass, field

from .formats import describe, weight_files
from .types import Chunk

MiB = 1 << 20
READ_BLOCK = 8 * MiB


def cut_points(size, header_end, tensors, chunk_bytes):
    """Chunk boundaries [0, ..., size] for one file (see module docstring)."""
    cuts, s = [0], 0

    def cut(at):
        nonlocal s
        cuts.append(at)
        s = at

    while header_end - s > chunk_bytes:
        cut(s + chunk_bytes)
    if header_end > s:
        cut(header_end)
    for t in tensors:
        o, e = t["offset"], t["offset"] + t["nbytes"]
        if e - s <= chunk_bytes:
            continue
        if o > s:
            cut(o)
        while e - s > chunk_bytes:
            cut(s + chunk_bytes)
    while size - s > chunk_bytes:
        cut(s + chunk_bytes)
    if size > s:
        cut(size)
    return cuts


@dataclass
class Manifest:
    model: str
    chunk_bytes: int
    files: dict                      # name -> {format, size, sha256, header, metadata, role}
    chunks: list                     # [types.Chunk], ordered by (file, offset)
    tensors: dict                    # name -> {file, offset, nbytes, dtype, shape, spans}
    build: dict = field(default_factory=dict)

    def chunks_of(self, file):
        return [c for c in self.chunks if c.file == file]

    def unique(self):
        """{chunk_id: nbytes} -- identical content is stored once."""
        return {c.id: c.nbytes for c in self.chunks}

    def validate(self):
        for name, f in self.files.items():
            cursor = 0
            for c in self.chunks_of(name):
                assert c.offset == cursor and c.nbytes > 0, f"gap/overlap in {name} at {cursor}"
                cursor += c.nbytes
            assert cursor == f["size"], f"{name}: covered {cursor} of {f['size']}"
        for name, t in self.tensors.items():
            assert sum(n for _, _, n in t["spans"]) == t["nbytes"], name

    def to_json(self):
        return {"model": self.model, "chunk_bytes": self.chunk_bytes, "files": self.files,
                "chunks": [[c.id, c.file, c.offset, c.nbytes, list(c.tensors)] for c in self.chunks],
                "tensors": self.tensors, "build": self.build}

    @classmethod
    def from_json(cls, d):
        chunks = [Chunk(i, f, o, n, tuple(t)) for i, f, o, n, t in d["chunks"]]
        tensors = {k: dict(v, spans=[tuple(s) for s in v["spans"]]) for k, v in d["tensors"].items()}
        return cls(d["model"], d["chunk_bytes"], d["files"], chunks, tensors, d.get("build", {}))


def build_manifest(model_dir, chunk_bytes=64 * MiB, sink=None, model=None):
    """Scan `model_dir` once, streaming. `sink(chunk, data)` receives each chunk's bytes (e.g. a
    store's put), so placing a model costs a single read of the source."""
    t0, read = time.time(), 0
    weights, rest = weight_files(model_dir)
    files, chunks, tensors = {}, [], {}
    for name in weights + rest:
        path = os.path.join(model_dir, name)
        table = describe(path) if name in weights else {"format": "raw", "header": (0, 0), "tensors": [],
                                                       "metadata": None, "size": os.path.getsize(path)}
        cuts = cut_points(table["size"], table["header"][1], table["tensors"], chunk_bytes)
        whole = hashlib.sha256()
        first = len(chunks)
        with open(path, "rb") as f:
            for a, b in zip(cuts, cuts[1:]):
                buf = bytearray(b - a)
                view, got = memoryview(buf), 0
                while got < len(buf):
                    n = f.readinto(view[got:got + READ_BLOCK])
                    if not n:
                        raise IOError(f"{path} shrank while reading")
                    got += n
                whole.update(buf)
                read += len(buf)
                chunks.append(Chunk(hashlib.sha256(buf).hexdigest(), name, a, b - a))
                if sink is not None:
                    sink(chunks[-1], buf)
        starts = cuts[:-1]
        inside = {}
        for t in table["tensors"]:
            spans, pos, end = [], t["offset"], t["offset"] + t["nbytes"]
            k = bisect.bisect_right(starts, pos) - 1
            while pos < end:
                c = chunks[first + k]
                n = min(end, c.offset + c.nbytes) - pos
                spans.append((first + k, pos - c.offset, n))
                inside.setdefault(first + k, []).append(t["name"])
                pos += n
                k += 1
            tensors[t["name"]] = {"file": name, "offset": t["offset"], "nbytes": t["nbytes"],
                                  "dtype": t["dtype"], "shape": t["shape"], "spans": spans}
        for k, names in inside.items():
            c = chunks[k]
            chunks[k] = Chunk(c.id, c.file, c.offset, c.nbytes, tuple(names))
        files[name] = {"format": table["format"], "size": table["size"], "sha256": whole.hexdigest(),
                       "header": list(table["header"]), "metadata": table.get("metadata"),
                       "role": "weights" if name in weights else "passthrough"}
    dt = time.time() - t0
    m = Manifest(model or os.path.basename(os.path.normpath(model_dir)), chunk_bytes, files, chunks, tensors,
                 {"seconds": dt, "bytes": read, "GBps": read / dt / 1e9 if dt else None})
    m.validate()
    return m


class MemChunkStore:
    """Chunks held in process memory (the DRAM tier for CPU-side tests and staging)."""

    def __init__(self):
        self.data = {}

    def put(self, cid, data):
        self.data.setdefault(cid, bytes(data))

    def get(self, cid):
        return memoryview(self.data[cid])

    def has(self, cid):
        return cid in self.data

    def delete(self, cid):
        self.data.pop(cid, None)

    def nbytes(self):
        return sum(len(v) for v in self.data.values())


class DirChunkStore:
    """Chunks as files named by content id under `root` (the SSD tier). Writes are atomic."""

    def __init__(self, root, fsync=False):
        self.root, self.fsync = root, fsync
        os.makedirs(root, exist_ok=True)

    def _path(self, cid):
        return os.path.join(self.root, cid[:2], cid)

    def put(self, cid, data):
        path = self._path(cid)
        if os.path.exists(path):
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp{os.getpid()}"
        with open(tmp, "wb") as f:
            f.write(data)
            if self.fsync:
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp, path)

    def get(self, cid):
        with open(self._path(cid), "rb") as f:
            return memoryview(f.read())

    def has(self, cid):
        return os.path.exists(self._path(cid))

    def delete(self, cid):
        try:
            os.remove(self._path(cid))
        except FileNotFoundError:
            pass


class ChunkedModel:
    """The user-visible view: original files and tensors, served from chunks wherever they live.

    `locate(chunk_id)` returns a store holding that chunk (local or remote; anything with get()).
    """

    def __init__(self, manifest, locate):
        self.m, self.locate = manifest, locate
        self._by_file = {}
        for i, c in enumerate(manifest.chunks):
            self._by_file.setdefault(c.file, []).append(i)
        self._starts = {f: [manifest.chunks[i].offset for i in idx] for f, idx in self._by_file.items()}

    def _chunk(self, i):
        c = self.m.chunks[i]
        return self.locate(c.id).get(c.id)

    def read(self, file, offset, n):
        if offset < 0 or offset + n > self.m.files[file]["size"]:
            raise ValueError("read beyond end of file")
        out, idx, starts = bytearray(), self._by_file[file], self._starts[file]
        k = bisect.bisect_right(starts, offset) - 1
        while n > 0:
            c = self.m.chunks[idx[k]]
            a = offset - c.offset
            take = min(n, c.nbytes - a)
            out += self._chunk(idx[k])[a:a + take]
            offset, n, k = offset + take, n - take, k + 1
        return bytes(out)

    def tensor_bytes(self, name):
        out = bytearray()
        for i, off, n in self.m.tensors[name]["spans"]:
            out += self._chunk(i)[off:off + n]
        return bytes(out)

    def iter_file(self, file):
        for i in self._by_file.get(file, ()):
            yield self._chunk(i)

    def materialize_file(self, file, out_path):
        h = hashlib.sha256()
        with open(out_path, "wb") as f:
            for part in self.iter_file(file):
                h.update(part)
                f.write(part)
        if h.hexdigest() != self.m.files[file]["sha256"]:
            raise IOError(f"{file}: reassembled bytes differ from the original")

    def materialize_dir(self, out_dir):
        """Rebuild the model directory exactly as the user handed it over."""
        os.makedirs(out_dir, exist_ok=True)
        for name in self.m.files:
            self.materialize_file(name, os.path.join(out_dir, name))

    def verify(self):
        """Every chunk hashes to its id and every file to its recorded sha256."""
        for name, f in self.m.files.items():
            whole = hashlib.sha256()
            for i in self._by_file.get(name, ()):
                data = self._chunk(i)
                if hashlib.sha256(data).hexdigest() != self.m.chunks[i].id:
                    raise IOError(f"chunk {i} of {name} is corrupt")
                whole.update(data)
            if whole.hexdigest() != f["sha256"]:
                raise IOError(f"{name}: file hash mismatch")
        return True


def save_manifest(manifest, path):
    with open(path, "w") as f:
        json.dump(manifest.to_json(), f)


def load_manifest(path):
    with open(path) as f:
        return Manifest.from_json(json.load(f))
