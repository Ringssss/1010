"""Validate bound pages, odd-sized transfers and explicit host-buffer teardown."""
import argparse
import json
from pathlib import Path
import resource
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "main_lib"))


def main(args):
    import torch
    from fluidckpt.memory import HostAllocation
    torch.cuda.set_device(args.gpu)
    torch.set_num_threads(1)
    rows = []
    for backend in ("torch_pinned_default", "numa_registered"):
        for size in (32 * 2**20, 32 * 2**20 + 17):
            owner = HostAllocation(size, backend, args.numa)
            try:
                owner.tensor.fill_(173)
                device = torch.empty(size, dtype=torch.uint8, device="cuda")
                device.copy_(owner.tensor, non_blocking=True)
                torch.cuda.synchronize()
                assert torch.all(device == 173).item()
                device.fill_(91)
                owner.tensor.copy_(device, non_blocking=True)
                torch.cuda.synchronize()
                assert torch.all(owner.tensor == 91).item()
                rows.append(dict(owner.info(), passed=True))
            finally:
                owner.close()
                owner.close()
            print(json.dumps(rows[-1]), flush=True)
    try:
        HostAllocation(1024, "numa_registered", 999)
        raise AssertionError("invalid NUMA node accepted")
    except (ValueError, MemoryError):
        pass
    Path(args.out).write_text(json.dumps({"rows": rows, "invalid_node_rejected": True}, indent=2))


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--numa", type=int, default=0)
    parser.add_argument("--out", required=True)
    main(parser.parse_args())
