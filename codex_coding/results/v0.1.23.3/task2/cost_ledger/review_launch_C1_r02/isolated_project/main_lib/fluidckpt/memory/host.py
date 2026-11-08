"""Owned, page-aligned host allocation. Registration never silently falls back."""
import mmap
import platform
import time

from .numa import mbind, placement


class HostAllocation:
    def __init__(self, nbytes, backend="torch_pinned_default", numa=None):
        import torch
        if nbytes <= 0 or backend not in ("torch_pinned_default", "numa_registered"):
            raise ValueError("positive size and a supported host backend are required")
        self.nbytes, self.backend = nbytes, backend
        self.numa = numa if backend == "numa_registered" else None
        self.tensor = None
        self.map = None
        self.registered = False
        self.closed = False
        self.timings = {}
        start = time.monotonic()
        if backend == "torch_pinned_default":
            self.tensor = torch.empty(nbytes, dtype=torch.uint8, pin_memory=True)
            self.allocated_bytes = None  # Tensor length does not expose allocator rounding.
            self.placement = None
        else:
            if platform.machine() != "x86_64" or numa is None:
                raise ValueError("NUMA backend currently requires Linux x86_64 and an explicit node")
            from fluidckpt.topology.discover import memory_node
            mem = memory_node(numa)
            available = mem.get("MemFree", 0) + mem.get("FilePages", 0) - mem.get("Shmem", 0)
            if nbytes + 2 * 2**30 > available:
                raise MemoryError(f"NUMA {numa}: requested {nbytes}, free+file-shmem={available}")
            self.allocated_bytes = (nbytes + mmap.PAGESIZE - 1) // mmap.PAGESIZE * mmap.PAGESIZE
            try:
                self.map = mmap.mmap(-1, self.allocated_bytes,
                                     flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
                # The new mapping has no resident pages; do not request migration privileges.
                mbind(self.map, numa, move=False)
                self.tensor = torch.frombuffer(self.map, dtype=torch.uint8, count=nbytes)
                old_threads = torch.get_num_threads()
                try:
                    torch.set_num_threads(1)
                    self.tensor.fill_(0)
                finally:
                    torch.set_num_threads(old_threads)
                touched = time.monotonic()
                self.timings["allocate_bind_touch_s"] = touched - start
                self.placement = placement(self.map, samples=256)
                if self.placement != {numa: 1.0}:
                    raise RuntimeError(f"NUMA placement mismatch: wanted {numa}, got {self.placement}")
                rc = torch.cuda.cudart().cudaHostRegister(self.tensor.data_ptr(), self.allocated_bytes, 0)
                if int(rc):
                    raise RuntimeError(f"host registration failed: {rc}")
                self.registered = True
                self.timings["register_s"] = time.monotonic() - touched
            except BaseException:
                self.close()
                raise
        self.timings["total_s"] = time.monotonic() - start

    def info(self):
        return {"backend": self.backend, "numa": self.numa, "logical_bytes": self.nbytes,
                "allocation_bytes": self.allocated_bytes, "sampled_page_distribution": self.placement,
                "placement_samples": 256 if self.map else 0, "timings": self.timings}

    def close(self):
        import torch
        if self.closed:
            return
        if self.registered:
            torch.cuda.synchronize()
            rc = torch.cuda.cudart().cudaHostUnregister(self.tensor.data_ptr())
            if int(rc):
                raise RuntimeError(f"host unregister failed: {rc}")
            self.registered = False
        self.tensor = None
        if self.map is not None:
            self.map.close()
            self.map = None
        self.closed = True
