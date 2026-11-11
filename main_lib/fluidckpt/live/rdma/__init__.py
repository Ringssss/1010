"""ctypes binding of rchunk.c (RDMA READ data plane). Stdlib only, so the DRAM/SSD side of a node
can run without torch. The shared object is built on first use next to the source."""
import ctypes
import os
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "rchunk.c")
_lib = None

# One IPoIB subnet per HCA port on this cluster: port p of node N is (12+p).11.3.N.
PORTS = 4


def port_ip(node_ip, port):
    """IPoIB address of HCA port `port` given the node's ib0 address."""
    a = node_ip.split(".")
    return ".".join([str(int(a[0]) + port)] + a[1:])


def lib():
    global _lib
    if _lib is not None:
        return _lib
    # Built per host architecture into a local (non-NFS-racy) name; rebuilt when the source changes.
    so = os.path.join(_HERE, "librchunk.so")
    if not os.path.exists(so) or os.path.getmtime(so) < os.path.getmtime(_SRC):
        tmp = f"{so}.{os.getpid()}.tmp"
        subprocess.check_call(["gcc", "-O2", "-fPIC", "-shared", "-o", tmp, _SRC,
                               "-lrdmacm", "-libverbs", "-lpthread"])
        os.replace(tmp, so)
    L = ctypes.CDLL(so)
    L.rc_init.restype = ctypes.c_int
    L.rc_dev_name.restype = ctypes.c_char_p
    L.rc_dev_name.argtypes = [ctypes.c_int]
    L.rc_reg.restype = ctypes.c_void_p
    L.rc_reg.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint64, ctypes.c_int,
                         ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
    L.rc_dereg.argtypes = [ctypes.c_void_p]
    L.rc_serve.argtypes = [ctypes.c_char_p, ctypes.c_int]
    L.rc_connect.restype = ctypes.c_void_p
    L.rc_connect.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    L.rc_conn_dev.argtypes = [ctypes.c_void_p]
    L.rc_read.restype = ctypes.c_int
    L.rc_read.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint32, ctypes.c_void_p,
                          ctypes.c_uint32, ctypes.c_uint64, ctypes.c_uint32, ctypes.c_int]
    L.rc_close.argtypes = [ctypes.c_void_p]
    L.rc_init()
    _lib = L
    return L


def devices():
    L = lib()
    return [L.rc_dev_name(i).decode() for i in range(L.rc_init())]


class Region:
    """A host memory range registered on one device."""

    def __init__(self, dev, addr, nbytes, remote):
        lk, rk = ctypes.c_uint32(0), ctypes.c_uint32(0)
        self.mr = lib().rc_reg(dev, addr, nbytes, int(remote), ctypes.byref(lk), ctypes.byref(rk))
        if not self.mr:
            raise OSError(ctypes.get_errno(), f"ibv_reg_mr failed (dev {dev}, {nbytes} bytes)")
        self.dev, self.addr, self.nbytes, self.lkey, self.rkey = dev, addr, nbytes, lk.value, rk.value

    def close(self):
        if self.mr:
            lib().rc_dereg(self.mr)
            self.mr = None


def serve(ip, port, wait_s=30.0):
    """Listen on ip:port. A predecessor that is still exiting may hold the port for a moment."""
    import time
    deadline = time.time() + wait_s
    while True:
        rc = lib().rc_serve(ip.encode(), port)
        if not rc:
            return
        if time.time() > deadline:
            raise OSError(f"rc_serve {ip}:{port} failed ({rc})")
        time.sleep(0.5)


class Conn:
    """One reliable connection; `read` blocks (GIL released), so use one thread per Conn."""

    def __init__(self, src_ip, dst_ip, port):
        self.h = lib().rc_connect(src_ip.encode(), dst_ip.encode(), port)
        if not self.h:
            raise OSError(f"rc_connect {src_ip} -> {dst_ip}:{port} failed")
        self.dev = lib().rc_conn_dev(self.h)

    def read(self, raddr, rkey, laddr, lkey, nbytes, piece=4 << 20, window=8):
        rc = lib().rc_read(self.h, raddr, rkey, laddr, lkey, nbytes, piece, window)
        if rc:
            raise OSError(f"rc_read failed ({rc})")

    def close(self):
        if self.h:
            lib().rc_close(self.h)
            self.h = None
