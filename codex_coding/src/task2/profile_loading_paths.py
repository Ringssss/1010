"""Measure bound DRAM -> GPU paths and simultaneous two-GPU contention."""
import argparse
import json
import multiprocessing as mp
import os
from pathlib import Path
import resource
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "main_lib"))


def worker(gpu, numa, sizes, barrier, queue):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        import torch
        from fluidckpt.memory import HostAllocation
        torch.cuda.set_device(gpu)
        torch.set_num_threads(1)
        rows = []
        for size in sizes:
            owner = HostAllocation(size, "numa_registered", numa)
            dst = torch.empty(size, dtype=torch.uint8, device=f"cuda:{gpu}")
            stream = torch.cuda.Stream(gpu)
            with torch.cuda.stream(stream):
                dst.copy_(owner.tensor, non_blocking=True)
            stream.synchronize()
            times = []
            for repeat in range(5):
                barrier.wait(timeout=60)
                start = time.monotonic()
                count = max(8, 2**30 // size)
                with torch.cuda.stream(stream):
                    for _ in range(count):
                        dst.copy_(owner.tensor, non_blocking=True)
                stream.synchronize()
                times.append((time.monotonic() - start) / count)
            assert torch.count_nonzero(dst).item() == 0
            rows.append({"gpu": gpu, "numa": numa, "bytes": size, "seconds": times,
                         "median_GBps": size / statistics.median(times) / 1e9,
                         "memory": owner.info()})
            del dst
            owner.close()
        queue.put({"rows": rows})
    except BaseException as exc:
        barrier.abort()
        queue.put({"error": repr(exc), "gpu": gpu, "numa": numa})


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    mp.set_start_method("spawn")
    parser = argparse.ArgumentParser()
    parser.add_argument("--topology", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    topo = json.loads(Path(args.topology).read_text())
    near = {g["logical_gpu"]: g["numa_node"] for g in topo["gpus"]}
    configs = {"local0": [(0, near[0])], "local1": [(1, near[1])],
               "remote0": [(0, near[1])], "remote1": [(1, near[0])],
               "concurrent_local": [(0, near[0]), (1, near[1])],
               "shared_numa0": [(0, near[0]), (1, near[0])],
               "shared_numa3": [(0, near[1]), (1, near[1])]}
    report = {"topology_fingerprint": topo["fingerprint"], "timestamp": time.time(),
              "environment": {k: os.environ.get(k) for k in ("AMD_SERIALIZE_KERNEL", "HIP_VISIBLE_DEVICES")},
              "repeats": 5, "scenarios": {},
              "scope": "2 GPUs; reused 16MiB/256MiB/1GiB buffers; excludes IPC mapping and storage scatter"}
    for name, pairs in configs.items():
        barrier, queue = mp.Barrier(len(pairs)), mp.Queue()
        processes = [mp.Process(target=worker, args=(g, n, [16 << 20, 256 << 20, 1 << 30], barrier, queue)) for g, n in pairs]
        try:
            for process in processes:
                process.start()
            rows = [queue.get(timeout=180) for _ in processes]
            if any("error" in row for row in rows):
                raise RuntimeError(str(rows))
            report["scenarios"][name] = rows
            Path(args.out).write_text(json.dumps(report, indent=2))
            print(json.dumps({"scenario": name, "GBps_1GiB": [r["rows"][-1]["median_GBps"] for r in rows]}), flush=True)
        finally:
            for process in processes:
                process.join(5)
                if process.is_alive():
                    process.kill(); process.join()
