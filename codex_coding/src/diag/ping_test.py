import os, socket, sys, time
import torch.distributed as dist
import torch
if __name__ == "__main__":
    dist.init_process_group(backend="gloo", timeout=__import__("datetime").timedelta(seconds=60))
    r, w = dist.get_rank(), dist.get_world_size()
    print(f"[{os.uname().nodename}] rank {r}/{w} gloo OK", flush=True)
    t = torch.tensor([r + 1.0])
    dist.all_reduce(t)
    print(f"[{os.uname().nodename}] all_reduce -> {t.item()}", flush=True)
    dist.destroy_process_group()
