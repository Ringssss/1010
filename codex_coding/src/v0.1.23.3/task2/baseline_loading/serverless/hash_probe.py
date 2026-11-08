"""Compare post-Store IPC hashing before/after explicit Torch device initialization."""
import argparse
import ctypes
import faulthandler
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import resource

from fluidckpt.benchmarks.serverless_adapter import UpstreamStoreLoader, atomic_json, hash_target


def trace(row):
    print(json.dumps(row), flush=True)


def owner(connection):
    import torch
    torch.cuda.set_device(0)
    tensors = [torch.full((size,), 204, dtype=torch.uint8, device="cuda:0") for size in (4096, 8192, 12288)]
    guards = [torch.full((4096,), 165, dtype=torch.uint8, device="cuda:0")]
    torch.cuda.synchronize()
    export = {"meta": {"bytes": 24576}, "entries": [{"name": f"storage{i}", "nbytes": t.numel(),
        "handle": t.untyped_storage()._share_cuda_()} for i, t in enumerate(tensors)]}
    connection.send(export)
    if connection.poll(120) and connection.recv() == "verify":
        digest = hashlib.sha256()
        for tensor in tensors:
            digest.update(tensor.cpu().numpy().tobytes())
        connection.send({"sha256": digest.hexdigest(), "guards_unchanged": bool(torch.all(guards[0] == 165))})
        if connection.poll(30):
            connection.recv()


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    faulthandler.enable(all_threads=True)
    p = argparse.ArgumentParser()
    p.add_argument("--case", choices=["current", "set_device"], required=True)
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
    connection, other = context.Pipe()
    producer = context.Process(target=owner, args=(other,))
    producer.start()
    loader = None
    try:
        if not connection.poll(60):
            raise TimeoutError("probe producer failed to initialize")
        export = connection.recv()
        loader = UpstreamStoreLoader(a.source_checkout, a.out / "data", 65536, 4096, 2)
        record = loader.start_rank("rank_0", index, export)
        loader.wait_rank(record)
        import torch
        runtime = ctypes.CDLL("/opt/dtk/hip/lib/libgalaxyhip.so")
        current = ctypes.c_int(-1)
        rc = runtime.hipGetDevice(ctypes.byref(current))
        trace({"event": "store_finished", "case": a.case, "torch_cuda_initialized": torch.cuda.is_initialized(),
               "hip_current_device": current.value, "hip_get_device_rc": rc,
               "torch_current_device": "not queried; Python current_device would initialize Torch and change the tested state"})
        if a.case == "set_device":
            torch.cuda.set_device(0)
            trace({"event": "explicit_set_device_finished", "torch_cuda_initialized": torch.cuda.is_initialized()})
        actual = hash_target(index, export, trace=trace)
        expected = hashlib.sha256(payload).hexdigest()
        connection.send("verify")
        if not connection.poll(30):
            raise TimeoutError("probe owner verification timed out")
        verified = connection.recv()
        passed = actual == expected == verified["sha256"] and verified["guards_unchanged"]
        atomic_json(a.out / "result.json", {"status": "passed" if passed else "failed", "case": a.case,
            "store": record, "target_sha256": actual, "source_sha256": expected, "owner": verified})
        if not passed:
            raise RuntimeError("probe full payload or guard mismatch")
    finally:
        if loader is not None:
            loader.close()
        try:
            connection.send("close")
        except (BrokenPipeError, EOFError):
            pass
        producer.join(5)
        if producer.is_alive():
            producer.terminate()
            producer.join()


if __name__ == "__main__":
    main()
