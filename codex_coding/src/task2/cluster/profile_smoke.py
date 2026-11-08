"""Check vendor tracing and dynamic capture before instrumenting a model restore."""
import ctypes
import resource

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
import torch

torch.cuda.set_device(0)
src = torch.empty(32 << 20, dtype=torch.uint8, pin_memory=True).fill_(37)
dst = torch.empty_like(src, device="cuda:0")
dst.copy_(src, non_blocking=True)
torch.cuda.synchronize()
tracer = ctypes.CDLL("libroctracer64.so")
tracer.roctracer_start.restype = None
tracer.roctracer_stop.restype = None
tracer.roctracer_start()
dst.copy_(src, non_blocking=True)
torch.cuda.synchronize()
tracer.roctracer_stop()
print("profile smoke passed", flush=True)
