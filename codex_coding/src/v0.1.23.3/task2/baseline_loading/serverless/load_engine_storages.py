"""Load TP ranks with upstream Store, publish engine completion, and hash all bytes."""
import argparse
import faulthandler
import json
from pathlib import Path
import pickle
import resource
import time

from fluidckpt.benchmarks.serverless_adapter import (
    UPSTREAM_SHA, UpstreamStoreLoader, atomic_json, hash_target,
)


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    faulthandler.enable(all_threads=True)
    p = argparse.ArgumentParser()
    p.add_argument("--source-checkout", type=Path, required=True)
    p.add_argument("--prepared-data", type=Path, required=True)
    p.add_argument("--target-handoff", type=Path, required=True)
    p.add_argument("--export-newer-than", type=float, required=True)
    p.add_argument("--pool-gib", type=int, default=66)
    p.add_argument("--chunk-mib", type=int, default=32)
    p.add_argument("--io-threads", type=int, default=4)
    p.add_argument("--timeout", type=float, default=600)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--verification-trigger", type=Path)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    prepared = json.loads(a.prepared_data.read_text())
    required_bytes = sum((row["file_bytes"] + (a.chunk_mib << 20)-1)
                         // (a.chunk_mib << 20) * (a.chunk_mib << 20) for row in prepared["ranks"])
    if required_bytes > a.pool_gib << 30:
        raise ValueError("concurrent upstream TP load needs the full rounded host cache")
    loader = UpstreamStoreLoader(a.source_checkout, prepared["data_root"], a.pool_gib << 30,
                                a.chunk_mib << 20, a.io_threads)
    result = {"status": "running", "upstream_sha": UPSTREAM_SHA,
        "scope": "actual upstream Store data plane with HIP and external engine IPC destinations",
        "pool_bytes": loader.pool_bytes, "pool_setup_s": loader.setup_s,
        "extra_hbm_staging_bytes": 0, "target_hbm_logical_bytes": sum(r["logical_bytes"] for r in prepared["ranks"]),
        "medium": prepared["medium"], "format_establishment_cache_state": prepared["cache_state"],
        "runtime_cache_state": "uncontrolled_unmeasured; direct-I/O attempt with upstream fallback",
        "ranks": [], "backend": loader.backend.__file__}
    atomic_json(a.out / "pool_ready.json", result)
    deadline = time.monotonic() + a.timeout
    exports = {}
    indexes = {}
    try:
        for row in prepared["ranks"]:
            rank = row["tp_rank"]
            path = a.target_handoff / f"rank{rank}.pkl"
            while not path.exists() or path.stat().st_mtime < a.export_newer_than:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"new target export not available: rank {rank}")
                time.sleep(.01)
            with path.open("rb") as stream:
                export = pickle.load(stream)
            meta = json.loads(Path(row["metadata"]).read_text())
            # Identity and tensor layout validation are mandatory before issuing any writes.
            from fluidckpt.serving.layout import validate_tensor_layout
            for key in ("model_identity", "tp_size", "tp_rank"):
                if export["meta"].get(key) != meta["meta"].get(key):
                    raise ValueError(f"source/target identity differs: {key}")
            validate_tensor_layout(meta["meta"]["tensors"], export["meta"]["tensors"])
            indexes[rank] = meta["index"]
            exports[rank] = export
        # All target metadata is checked before scheduling either rank's byte transfer.
        for row in prepared["ranks"]:
            rank = row["tp_rank"]
            record = loader.start_rank(row["model_path"], indexes[rank], exports[rank])
            record["tp_rank"] = rank
            result["ranks"].append(record)
        for record in result["ranks"]:
            loader.wait_rank(record)
            rank = record["tp_rank"]
            # Completion unblocks the existing engine; no second target weight allocation.
            atomic_json(a.target_handoff / f"rank{rank}.done", {"loader": "ServerlessLLM Store HIP",
                "upstream_sha": UPSTREAM_SHA, "store_ready_unix_s": record["store_ready_unix_s"]})
        result["store_window_s"] = (max(r["store_ready_unix_s"] for r in result["ranks"])
                                    - min(r["load_begin_unix_s"] for r in result["ranks"]))
        atomic_json(a.out / "store_ready.json", result)
        if a.verification_trigger:
            deadline = time.monotonic() + a.timeout
            while not a.verification_trigger.exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("post-generation verification trigger missing")
                time.sleep(.01)
        for row in prepared["ranks"]:
            rank = row["tp_rank"]
            started = time.time()
            def verification_trace(event):
                print(json.dumps({"phase": "post_ready_verification", "tp_rank": rank, **event}), flush=True)
            actual = hash_target(indexes[rank], exports[rank], trace=verification_trace)
            record = next(r for r in result["ranks"] if r["tp_rank"] == rank)
            record.update(verification_s=time.time()-started, target_sha256=actual,
                          source_sha256=row["payload_sha256"],
                          full_sha256_equal=actual == row["payload_sha256"])
            if not record["full_sha256_equal"]:
                raise RuntimeError(f"full payload SHA mismatch: rank {rank}")
        result["status"] = "passed"
    except Exception as error:
        result["status"] = "failed"
        result["error"] = repr(error)
        raise
    finally:
        atomic_json(a.out / "result.json", result)
        loader.close()


if __name__ == "__main__":
    main()
