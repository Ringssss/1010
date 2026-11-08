"""Read the immutable keeper HostAllocation via /proc/mem into one NFS output copy."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import time
from urllib.request import ProxyHandler, build_opener

from full_protocol import frozen_rank_hashes

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
p = argparse.ArgumentParser()
p.add_argument("--session", type=Path, required=True)
p.add_argument("--data-root", type=Path, required=True)
p.add_argument("--out", type=Path, required=True)
p.add_argument("--frozen-source", type=Path, required=True)
p.add_argument("--expected-pids", default="118,136")
a = p.parse_args()
state = json.loads(a.session.read_text())
model = state["models"]["B"]
root = json.loads(Path(state["root_session"]).read_text())
expected = frozen_rank_hashes(a.frozen_source, model)
expected_pids = [int(value) for value in a.expected_pids.split(",")]
if len(expected_pids) != 2:
    raise ValueError("this exporter is restricted to the two explicitly named fixture workers")
allowed = Path("/data/node28/shared/zhujianian/fluidckpt_zjn_baseline_data/v0.1.23.3/task2/serverless_data_r01")
if allowed not in a.data_root.resolve().parents:
    raise ValueError("use a fresh generation beneath the approved unique data run")
a.data_root.mkdir(parents=True, exist_ok=False)
a.out.mkdir(parents=True, exist_ok=False)
opener = build_opener(ProxyHandler({}))
def inventory(worker):
    with opener.open(root["endpoints"][worker] + "/inventory?compact=1", timeout=15) as response:
        return json.load(response)

result = {"status": "running", "data_root": str(a.data_root), "medium": "NFS /data/node28/shared",
    "session": str(a.session), "frozen_source_evidence": str(a.frozen_source),
    "cache_state": "newly written; source process stream fully SHA-hashed; no cache drop",
    "export_route": "CPU-only /proc/PID/mem read of exact immutable HostAllocation mmap; no source writes/ptrace attach/stop",
    "extra_hbm_bytes": 0, "stream_buffer_bytes": 64 << 20, "ranks": []}
try:
    for source in sorted(model["sources"], key=lambda row: row["tp"]):
        started = time.time()
        rank = source["tp"]
        before = inventory(source["worker"])
        rows = [r for r in before["replicas"] if r["id"] == source["id"]]
        if (len(rows) != 1 or rows[0]["state"] != "VALID" or rows[0]["n"] != source["n"]
                or rows[0]["tier"] != "dram" or rows[0]["identity"] != source["identity"]):
            raise ValueError("the exact frozen DRAM source is not available")
        pid = before["pid"]
        if pid != expected_pids[rank]:
            raise ValueError("source PID differs from the explicitly selected fixture")
        starttime = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        candidates = []
        for line in Path(f"/proc/{pid}/maps").read_text().splitlines():
            parts = line.split()
            begin, end = (int(value, 16) for value in parts[0].split("-"))
            if len(parts) == 5 and parts[1] == "rw-p" and end-begin == ((source["n"]+4095)//4096)*4096:
                candidates.append((begin, end, line))
        if len(candidates) != 1:
            raise ValueError("source does not have one uniquely sized anonymous full-rank mmap")
        address, end, mapping_line = candidates[0]
        rank_dir = a.data_root / f"rank_{rank}"
        rank_dir.mkdir()
        data_file = rank_dir / "tensor.data_0"
        digest = hashlib.sha256()
        fd = os.open(f"/proc/{pid}/mem", os.O_RDONLY)
        copied = 0
        try:
            with data_file.open("xb", buffering=0) as stream:
                while copied < source["n"]:
                    block = os.pread(fd, min(64 << 20, source["n"]-copied), address+copied)
                    if not block:
                        raise EOFError("source process mapping ended before a complete rank")
                    if stream.write(block) != len(block):
                        raise OSError("incomplete NFS file write")
                    digest.update(block)
                    copied += len(block)
                    if copied % (1 << 30) == 0 or copied == source["n"]:
                        print(json.dumps({"rank": rank, "copied_bytes": copied, "elapsed_s": time.time()-started}), flush=True)
        finally:
            os.close(fd)
        source_sha = digest.hexdigest()
        if source_sha != expected[rank]:
            raise ValueError(f"rank {rank} read mapping differs from the S2 frozen full-rank SHA")
        after = inventory(source["worker"])
        end_starttime = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        if before["epoch"] != after["epoch"] or before["replicas"] != after["replicas"] or starttime != end_starttime:
            raise ValueError("source epoch/catalog changed during CPU export")
        pad = (-source["n"]) % 4096
        with data_file.open("ab") as stream:
            stream.write(bytes(pad))
        metadata = {"meta": source["meta"], "index": source["index"]}
        (rank_dir / "processed_layout.json").write_text(json.dumps(metadata))
        (rank_dir / "tensor_index.json").write_text(json.dumps({name: [off, size, [size], [1], "torch.uint8"]
            for name, (off, size) in source["index"].items()}))
        result["ranks"].append({"tp_rank": rank, "model_path": rank_dir.name,
            "data_file": str(data_file), "metadata": str(rank_dir / "processed_layout.json"),
            "logical_bytes": source["n"], "file_bytes": source["n"]+pad, "padding_bytes": pad,
            "payload_sha256": source_sha, "frozen_source_sha256": expected[rank], "frozen_source_equal": True,
            "source_epoch": before["epoch"], "source_pid": pid, "mapping_line": mapping_line,
            "source_starttime_ticks": starttime, "source_starttime_unchanged": True,
            "source_metadata_unchanged": True, "format_setup_s": time.time()-started})
        (a.out / "prepared_data.json").write_text(json.dumps(result, indent=2))
    result["status"] = "passed"
except BaseException as error:
    result["status"] = "failed"
    result["error"] = repr(error)
    raise
finally:
    (a.out / "prepared_data.json").write_text(json.dumps(result, indent=2))
