"""Format adapters: turn a weight file into a uniform tensor table (stdlib only).

Every adapter returns, per file, the header byte range and a list of tensors with *absolute*
file offsets, so the chunker can cut on tensor boundaries without knowing the format:
    {"file", "format", "size", "header": (0, data_start), "tensors": [{name, offset, nbytes, dtype, shape}]}

safetensors: u64 LE header length, JSON header {name: {dtype, shape, data_offsets}} relative to
    the data section (8 + header length); "__metadata__" is not a tensor.
GGUF v2/v3: magic, version, tensor_count, kv_count, KV metadata, tensor infos
    (name, n_dims, dims, ggml type, offset relative to the aligned data section). Tensor sizes come
    from the ggml block table; an unknown type is sized by the gap to the next tensor and flagged.
raw: anything else is one opaque blob (still chunked and placed, just not tensor-addressable).
"""
import json
import os
import struct

ST_DTYPE_BYTES = {"F64": 8, "F32": 4, "F16": 2, "BF16": 2, "I64": 8, "I32": 4, "I16": 2, "I8": 1,
                  "U8": 1, "U16": 2, "U32": 4, "U64": 8, "BOOL": 1, "F8_E4M3": 1, "F8_E5M2": 1}

# ggml type id -> (name, block elements, block bytes)
GGML_TYPES = {
    0: ("F32", 1, 4), 1: ("F16", 1, 2), 2: ("Q4_0", 32, 18), 3: ("Q4_1", 32, 20),
    6: ("Q5_0", 32, 22), 7: ("Q5_1", 32, 24), 8: ("Q8_0", 32, 34), 9: ("Q8_1", 32, 36),
    10: ("Q2_K", 256, 84), 11: ("Q3_K", 256, 110), 12: ("Q4_K", 256, 144), 13: ("Q5_K", 256, 176),
    14: ("Q6_K", 256, 210), 15: ("Q8_K", 256, 292), 16: ("IQ2_XXS", 256, 66), 17: ("IQ2_XS", 256, 74),
    18: ("IQ3_XXS", 256, 98), 19: ("IQ1_S", 256, 50), 20: ("IQ4_NL", 32, 18), 21: ("IQ3_S", 256, 110),
    22: ("IQ2_S", 256, 82), 23: ("IQ4_XS", 256, 136), 24: ("I8", 1, 1), 25: ("I16", 1, 2),
    26: ("I32", 1, 4), 27: ("I64", 1, 8), 28: ("F64", 1, 8), 29: ("IQ1_M", 256, 56), 30: ("BF16", 1, 2),
}
# GGUF metadata value types: id -> struct format (None = variable length)
GGUF_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
GGUF_STRING, GGUF_ARRAY = 8, 9


def safetensors_table(path):
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
    base = 8 + n
    tensors = []
    for name, info in header.items():
        if name == "__metadata__":
            continue
        a, b = info["data_offsets"]
        tensors.append({"name": name, "offset": base + a, "nbytes": b - a, "dtype": info["dtype"],
                        "shape": info["shape"]})
    tensors.sort(key=lambda t: t["offset"])
    return {"format": "safetensors", "header": (0, base), "tensors": tensors,
            "metadata": header.get("__metadata__")}


class _Reader:
    def __init__(self, f):
        self.f = f

    def take(self, fmt):
        size = struct.calcsize(fmt)
        data = self.f.read(size)
        if len(data) != size:
            raise ValueError("truncated GGUF")
        return struct.unpack(fmt, data)[0]

    def string(self):
        n = self.take("<Q")
        return self.f.read(n).decode("utf-8", "replace")

    def value(self, vtype):
        if vtype in GGUF_SCALARS:
            return self.take(GGUF_SCALARS[vtype])
        if vtype == GGUF_STRING:
            return self.string()
        if vtype == GGUF_ARRAY:
            etype, count = self.take("<I"), self.take("<Q")
            if etype in GGUF_SCALARS:          # read scalar arrays in one call (vocab scores etc.)
                fmt = GGUF_SCALARS[etype]
                data = self.f.read(struct.calcsize(fmt) * count)
                return list(struct.unpack("<%d%s" % (count, fmt[1:]), data))
            return [self.value(etype) for _ in range(count)]
        raise ValueError(f"unknown GGUF value type {vtype}")


def gguf_table(path, keep_metadata=("general.architecture", "general.name", "general.alignment")):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        r = _Reader(f)
        if f.read(4) != b"GGUF":
            raise ValueError("not a GGUF file")
        version = r.take("<I")
        if version not in (2, 3):
            raise ValueError(f"unsupported GGUF version {version}")
        n_tensors, n_kv = r.take("<Q"), r.take("<Q")
        meta = {}
        for _ in range(n_kv):
            key = r.string()
            val = r.value(r.take("<I"))
            if key in keep_metadata:
                meta[key] = val
        infos = []
        for _ in range(n_tensors):
            name = r.string()
            dims = [r.take("<Q") for _ in range(r.take("<I"))]
            infos.append((name, dims, r.take("<I"), r.take("<Q")))
        align = int(meta.get("general.alignment", 32))
        data_start = (f.tell() + align - 1) // align * align
    order = sorted(range(len(infos)), key=lambda i: infos[i][3])
    tensors = []
    for rank, i in enumerate(order):
        name, dims, gtype, rel = infos[i]
        n_el = 1
        for d in dims:
            n_el *= d
        entry = {"name": name, "offset": data_start + rel, "shape": dims}
        if gtype in GGML_TYPES:
            tname, blk, bb = GGML_TYPES[gtype]
            entry.update(dtype=tname, nbytes=n_el // blk * bb)
        else:                                   # size by the gap to the next tensor
            nxt = data_start + infos[order[rank + 1]][3] if rank + 1 < len(order) else size
            entry.update(dtype=f"ggml_{gtype}", nbytes=nxt - entry["offset"], size_inferred=True)
        tensors.append(entry)
    return {"format": "gguf", "header": (0, data_start), "tensors": tensors, "metadata": meta,
            "version": version}


def raw_table(path):
    return {"format": "raw", "header": (0, 0), "tensors": [], "metadata": None}


def describe(path):
    """Tensor table of one file, chosen by content (magic) rather than by extension."""
    with open(path, "rb") as f:
        head = f.read(8)
    if head[:4] == b"GGUF":
        table = gguf_table(path)
    elif path.endswith(".safetensors") and len(head) == 8:
        table = safetensors_table(path)
    else:
        table = raw_table(path)
    table["size"] = os.path.getsize(path)
    return table


def weight_files(model_dir):
    """(weight files, passthrough files) of a model directory, relative paths, sorted.

    A safetensors index lists the shards explicitly; otherwise every *.safetensors / *.gguf file
    is a weight file. Everything else (config, tokenizer) is passed through untouched.
    """
    names = sorted(os.listdir(model_dir))
    index = [n for n in names if n.endswith(".safetensors.index.json")]
    if index:
        with open(os.path.join(model_dir, index[0])) as f:
            weights = sorted(set(json.load(f)["weight_map"].values()))
    else:
        weights = [n for n in names if n.endswith((".safetensors", ".gguf"))]
    rest = [n for n in names if n not in weights and os.path.isfile(os.path.join(model_dir, n))]
    return weights, rest
