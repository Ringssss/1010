"""Build 64 MiB chunk manifests for the catalog (run on the node that holds the files).
usage: build_manifests.py <out_dir> name=model_dir [name=model_dir ...]   (one process per model)"""
import json, os, sys, time
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, "/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib")
from fluidckpt.live.chunks import build_manifest, save_manifest

def one(arg):
    out, name, d = arg
    path = os.path.join(out, name + ".json")
    if os.path.exists(path):
        return name, "exists"
    try:
        m = build_manifest(d, chunk_bytes=64 << 20, model=name)
    except Exception as e:
        return name, {"error": repr(e)}
    m.build["model_dir"] = d
    save_manifest(m, path)
    return name, {"chunks": len(m.chunks), "unique": len({c.id for c in m.chunks}),
                  "GiB": round(sum(f["size"] for f in m.files.values()) / 2**30, 2), "s": round(m.build["seconds"], 1)}

if __name__ == "__main__":
    out = sys.argv[1]
    jobs = [(out, *a.split("=", 1)) for a in sys.argv[2:]]
    with ProcessPoolExecutor(len(jobs)) as ex:
        for name, info in ex.map(one, jobs):
            print(json.dumps({"model": name, "info": info}), flush=True)
