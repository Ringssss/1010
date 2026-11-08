"""NUMA-placed, host-registered buffers (x86_64 Linux syscalls, no libnuma needed)."""
import ctypes
import mmap

_libc = ctypes.CDLL(None, use_errno=True)
_libc.syscall.restype = ctypes.c_long
SYS_mbind, SYS_get_mempolicy = 237, 239
MPOL_BIND = 2
MPOL_MF_STRICT, MPOL_MF_MOVE = 1, 2
MPOL_F_NODE, MPOL_F_ADDR = 1, 2
PAGE = mmap.PAGESIZE


def _addr(buf):
    return ctypes.addressof(ctypes.c_char.from_buffer(buf))


def mbind(buf, node, move=True):
    if node < 0 or node >= 64:
        raise ValueError("NUMA node must fit the 64-bit nodemask")
    mask = ctypes.c_ulong(1 << node)
    rc = _libc.syscall(ctypes.c_long(SYS_mbind), ctypes.c_void_p(_addr(buf)), ctypes.c_ulong(len(buf)),
                       ctypes.c_int(MPOL_BIND), ctypes.byref(mask), ctypes.c_ulong(64),
                       ctypes.c_uint(MPOL_MF_STRICT | MPOL_MF_MOVE if move else 0))
    if rc != 0:
        raise OSError(ctypes.get_errno(), f"mbind(node={node}) failed")


def node_of(addr):
    node = ctypes.c_int(-1)
    rc = _libc.syscall(ctypes.c_long(SYS_get_mempolicy), ctypes.byref(node), None, ctypes.c_ulong(0),
                       ctypes.c_void_p(addr), ctypes.c_ulong(MPOL_F_NODE | MPOL_F_ADDR))
    if rc != 0:
        raise OSError(ctypes.get_errno(), "get_mempolicy failed")
    return node.value


def placement(buf, samples=64):
    """Fraction of sampled pages that live on each NUMA node."""
    base, n = _addr(buf), len(buf)
    counts = {}
    for i in range(samples):
        node = node_of(base + (i * n // samples) // PAGE * PAGE)
        counts[node] = counts.get(node, 0) + 1
    return {k: v / samples for k, v in sorted(counts.items())}


class HostBuffer:
    """Anonymous mmap bound to one NUMA node, first-touched, optionally HIP-registered."""

    def __init__(self, nbytes, node, register=True):
        import torch
        self.nbytes, self.node = nbytes, node
        self.map = mmap.mmap(-1, nbytes, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
        mbind(self.map, node)
        self.tensor = torch.frombuffer(self.map, dtype=torch.uint8)
        self.tensor.fill_(1)  # first touch under the bind policy
        self.registered = False
        if register:
            rc = torch.cuda.cudart().cudaHostRegister(self.tensor.data_ptr(), nbytes, 0)
            if int(rc) != 0:
                raise RuntimeError(f"hipHostRegister rc={rc}")
            self.registered = True

    def placement(self):
        return placement(self.map)

    def close(self):
        import torch
        if self.registered:
            torch.cuda.cudart().cudaHostUnregister(self.tensor.data_ptr())
            self.registered = False
        del self.tensor
        self.map.close()
