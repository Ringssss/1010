"""Run the actual upstream loader into IPC allocations owned by a separate process."""
import argparse
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import pickle
import resource
import time

from fluidckpt.benchmarks.serverless_adapter import (
    UPSTREAM_SHA, UpstreamStoreLoader, destination_plan, atomic_json,
)


def target_owner(connection, data_root):
    import torch
    torch.cuda.set_device(0)
    # Small independent storages share one caching allocator segment at different offsets.
    sizes = [4096, 8192, 12288]
    guards = [torch.full((4096,), 165, dtype=torch.uint8, device="cuda:0")]
    tensors = []
    entries = []
    for i, size in enumerate(sizes):
        tensor = torch.full((size,), 204, dtype=torch.uint8, device="cuda:0")
        tensors.append(tensor)
        storage = tensor.untyped_storage()
        entries.append({"name": f"storage{i}", "nbytes": size,
                        "handle": storage._share_cuda_()})
    guards.append(torch.full((4096,), 165, dtype=torch.uint8, device="cuda:0"))
    torch.cuda.synchronize()
    connection.send({"entries": entries, "meta": {"bytes": sum(sizes)}})
    if connection.poll(120) and connection.recv() == "verify":
        digest = hashlib.sha256()
        equal = []
        payload = (Path(data_root) / "rank_0/tensor.data_0").read_bytes()
        offset = 0
        for tensor in tensors:
            actual = tensor.cpu().numpy().tobytes()
            equal.append(actual == payload[offset:offset+len(actual)])
            digest.update(actual)
            offset += len(actual)
        connection.send({"payload_sha256": digest.hexdigest(), "all_bytes_equal": all(equal),
                         "guards_unchanged": all(bool(torch.all(t == 165)) for t in guards)})
        connection.poll(30)


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    p = argparse.ArgumentParser()
    p.add_argument("--source-checkout", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    rank_dir = a.out / "data/rank_0"
    rank_dir.mkdir(parents=True)
    payload = bytes((i * 13 + i // 17) % 256 for i in range(24576))
    (rank_dir / "tensor.data_0").write_bytes(payload)
    index = {"storage0": (0, 4096), "storage1": (4096, 8192), "storage2": (12288, 12288)}
    context = mp.get_context("spawn")
    connection, child_connection = context.Pipe()
    owner = context.Process(target=target_owner, args=(child_connection, a.out / "data"))
    owner.start()
    loader = None
    try:
        if not connection.poll(90):
            raise TimeoutError("IPC target owner initialization timed out")
        export = connection.recv()
        with (a.out / "target.pkl").open("wb") as stream:
            pickle.dump(export, stream)
        plan = destination_plan(index, export)
        if not plan["nonzero_allocation_offsets"] or plan["allocation_count"] >= plan["storage_count"]:
            raise RuntimeError("gate did not exercise nonzero offsets and shared allocation handles")
        loader = UpstreamStoreLoader(a.source_checkout, a.out / "data", 65536, 4096, 2)
        record = loader.start_rank("rank_0", index, export)
        loader.wait_rank(record)
        connection.send("verify")
        if not connection.poll(60):
            raise TimeoutError("full byte verification timed out")
        verified = connection.recv()
        expected = hashlib.sha256(payload).hexdigest()
        passed = (verified["payload_sha256"] == expected and verified["all_bytes_equal"]
                  and verified["guards_unchanged"])
        atomic_json(a.out / "result.json", {"status": "passed" if passed else "failed",
            "upstream_sha": UPSTREAM_SHA, "loader": record, "verification": verified,
            "source_sha256": expected, "pool_setup_s": loader.setup_s,
            "backend": loader.backend.__file__, "total_logical_bytes": len(payload)})
        if not passed:
            raise RuntimeError("actual upstream offset gate failed")
    finally:
        if loader is not None:
            loader.close()
        connection.send("close")
        owner.join(10)
        if owner.is_alive():
            owner.terminate()
            owner.join()


if __name__ == "__main__":
    main()
