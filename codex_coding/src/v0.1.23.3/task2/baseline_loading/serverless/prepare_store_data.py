"""Export keeper DRAM once into upstream partition format on the same NFS medium."""
import argparse
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen
from fifo_stream import export_fifo_to_file
from full_protocol import frozen_rank_hashes

p = argparse.ArgumentParser()
p.add_argument("--session", type=Path, required=True)
p.add_argument("--model", default="B")
p.add_argument("--snap", default="basic_B")
p.add_argument("--data-root", type=Path, required=True)
p.add_argument("--out", type=Path, required=True)
p.add_argument("--frozen-source", type=Path, required=True)
a = p.parse_args()
state = json.loads(a.session.read_text())
root = json.loads(Path(state["root_session"]).read_text())
model = state["models"][a.model]
frozen_hashes = frozen_rank_hashes(a.frozen_source, model)
prefix = Path("/data/node28/shared/zhujianian/fluidckpt_zjn_baseline_data/v0.1.23.3/task2")
if prefix not in a.data_root.resolve().parents:
    raise ValueError("store data must use a new approved NFS run directory")
a.data_root.mkdir(parents=True, exist_ok=False)
a.out.mkdir(parents=True, exist_ok=False)
rows = []
def save_source(request):
    with urlopen(request, timeout=1800) as response:
        return json.load(response)

for source in sorted(model["sources"], key=lambda row: row["tp"]):
    rank = source["tp"]
    endpoint = root["endpoints"][source["worker"]]
    started = time.time()
    # The source container's NFS mount is read-only. Its existing save writes through a FIFO
    # in the shared writable repository; this CPU consumer streams directly to NFS.
    scratch = a.out / f"export_rank{rank}"
    scratch.mkdir()
    fifo = scratch / f"rank{rank}.bin"
    rank_dir = a.data_root / f"rank_{rank}"
    rank_dir.mkdir()
    data_file = rank_dir / "tensor.data_0"
    request = Request(endpoint + "/save", data=json.dumps({"snap": a.snap,
        "dir": str(scratch.resolve())}).encode(), headers={"Content-Type": "application/json"})
    source_sha256, saved = export_fifo_to_file(fifo, data_file, source["n"],
                                              lambda: save_source(request))
    if source_sha256 != frozen_hashes[rank]:
        (a.out / f"failed_rank{rank}.json").write_text(json.dumps({"status": "failed",
            "reason": "exported payload SHA differs from frozen S2 source",
            "source_sha256": source_sha256, "frozen_sha256": frozen_hashes[rank]}, indent=2))
        raise ValueError("exported payload SHA differs from frozen S2 source")
    metadata = json.loads((scratch / f"rank{rank}.json").read_text())
    logical_bytes = max(offset+size for offset, size in metadata["index"].values())
    if logical_bytes != source["n"]:
        raise ValueError("keeper exported an unexpected full-rank size")
    if data_file.stat().st_size != logical_bytes:
        raise ValueError("keeper file is incomplete")
    pad = (-logical_bytes) % 4096
    with data_file.open("ab") as stream:
        stream.write(bytes(pad))
    tensor_index = {name: [offset, size, [size], [1], "torch.uint8"]
                    for name, (offset, size) in metadata["index"].items()}
    (rank_dir / "tensor_index.json").write_text(json.dumps(tensor_index))
    (rank_dir / "processed_layout.json").write_text(json.dumps(metadata))
    rows.append({"tp_rank": rank, "model_path": rank_dir.name, "data_file": str(data_file),
                 "metadata": str(rank_dir / "processed_layout.json"), "logical_bytes": logical_bytes,
                 "file_bytes": logical_bytes+pad, "padding_bytes": pad,
                 "payload_sha256": source_sha256, "format_setup_s": time.time()-started,
                 "frozen_source_sha256": frozen_hashes[rank], "frozen_source_equal": True,
                 "keeper_save": saved})
    (a.out / "prepared_data.json").write_text(json.dumps({"data_root": str(a.data_root),
        "session": str(a.session), "cache_state": "newly written; source stream fully SHA-hashed; no cache drop",
        "frozen_source_evidence": str(a.frozen_source),
        "medium": "NFS /data/node28/shared", "ranks": rows}, indent=2))
