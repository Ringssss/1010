"""Check pinned allocation rounding across a power-of-two boundary in fresh processes."""
import json
import multiprocessing as mp
from pathlib import Path
import resource
import time

ROOT = Path(__file__).resolve().parents[3]


def probe(size, queue):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    import torch
    # Warm the runtime separately; this backend exposes empty allocator statistics.
    warmup = torch.empty(1, dtype=torch.uint8, pin_memory=True)

    def physical():
        fields = {}
        for line in Path("/proc/self/smaps_rollup").read_text().splitlines()[1:]:
            key, value = line.split(":", 1)
            fields[key] = int(value.split()[0]) * 1024
        return fields

    before = torch.cuda.memory.host_memory_stats()
    physical_before = physical()
    start = time.monotonic()
    tensor = torch.empty(size, dtype=torch.uint8, pin_memory=True)
    elapsed = time.monotonic() - start
    after = torch.cuda.memory.host_memory_stats()
    physical_after = physical()
    pointer = tensor.data_ptr()
    mapping = next(line for line in Path("/proc/self/maps").read_text().splitlines()
                   if int(line.split()[0].split("-")[0], 16) <= pointer <
                   int(line.split()[0].split("-")[1], 16))
    queue.put({"requested_bytes": size, "tensor_bytes": tensor.numel(),
               "allocation_s": elapsed, "is_pinned": tensor.is_pinned(),
               "before": before, "after": after,
               "physical_delta_bytes": {k: physical_after[k] - physical_before[k]
                                        for k in ("Rss", "Pss", "Anonymous")},
               "allocation_mapping": mapping})


if __name__ == "__main__":
    mp.set_start_method("spawn")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    rows = []
    for size in (32 * 2**20, 32 * 2**20 + 1):
        queue = mp.Queue()
        process = mp.Process(target=probe, args=(size, queue))
        process.start()
        row = queue.get(timeout=90)
        process.join(15)
        if process.is_alive():
            process.kill()
            process.join()
            raise TimeoutError("probe failed to exit")
        assert process.exitcode == 0
        rows.append(row)
        print(json.dumps(row), flush=True)
    path = ROOT / "codex_coding/results/task2/loop03/pinned_rounding_physical_probe.json"
    path.write_text(json.dumps(rows, indent=2))
