"""Merge per-group manifests into one model manifest (files are disjoint; chunk indices in the
tensor spans are shifted). usage: merge_manifests.py <out.json> <name> <model_dir> part.json..."""
import json, sys
out, name, model_dir, parts = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
m = {"model": name, "chunk_bytes": None, "files": {}, "chunks": [], "tensors": {}, "build": {"model_dir": model_dir, "merged_from": parts, "bytes": 0, "seconds": 0}}
for p in parts:
    d = json.load(open(p)); base = len(m["chunks"])
    assert m["chunk_bytes"] in (None, d["chunk_bytes"]); m["chunk_bytes"] = d["chunk_bytes"]
    assert not set(d["files"]) & set(m["files"]) - {"config.json"}, "file names collide"
    weights = {f for f, v in d["files"].items() if v["role"] == "weights"}
    m["files"].update({f: v for f, v in d["files"].items() if f in weights or f not in m["files"]})
    keep = [i for i, c in enumerate(d["chunks"]) if c[1] in weights or not any(x[1] == c[1] for x in m["chunks"])]
    remap = {i: base + k for k, i in enumerate(keep)}
    m["chunks"] += [d["chunks"][i] for i in keep]
    for t, v in d["tensors"].items():
        assert t not in m["tensors"], f"tensor {t} appears twice"
        v["spans"] = [[remap[s[0]], s[1], s[2]] for s in v["spans"]]
        m["tensors"][t] = v
    m["build"]["bytes"] += d["build"].get("bytes", 0); m["build"]["seconds"] += d["build"].get("seconds", 0)
json.dump(m, open(out, "w"))
w = sum(v["size"] for v in m["files"].values() if v["role"] == "weights")
print(json.dumps({"model": name, "files": len(m["files"]), "chunks": len(m["chunks"]), "weights_GiB": round(w / 2**30, 2), "tensors": len(m["tensors"])}))
