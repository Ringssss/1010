"""Minimal 2-node RCCL smoke test: does the communicator initialise at all, and over what transport?"""
import os
import torch
import torch.distributed as dist

if __name__ == "__main__":
    dist.init_process_group(backend="nccl", timeout=__import__("datetime").timedelta(seconds=90))
    r = dist.get_rank()
    torch.cuda.set_device(int(os.environ.get("LOCAL_RANK", 0)))
    print(f"[{os.uname().nodename}] rank {r} PG up", flush=True)
    t = torch.ones(1 << 20, device="cuda")
    dist.all_reduce(t)
    torch.cuda.synchronize()
    print(f"[{os.uname().nodename}] all_reduce ok -> {t[0].item()}", flush=True)
    dist.destroy_process_group()
