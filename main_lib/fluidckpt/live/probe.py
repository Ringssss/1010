"""Per-node, read-only probe of storage media and links (stdlib only).

Runs locally or piped to a peer: `ssh node28 python3 - --samples 5 < probe.py`. Nothing is
written on the probed node and no GPU context is created: HBM state comes from the SMI library,
which reads driver counters without allocating device memory.

Two sampling rates, because the sources differ by three orders of magnitude in cost:
  fast (~10 Hz, < 2 ms): HBM used per GPU (rsmi_dev_memory_usage_get, ~0.06 ms/GPU), DRAM per
      NUMA (/sys/devices/system/node), NVMe (/proc/diskstats deltas + statvfs), IB HCA
      (/sys/class/infiniband counters, units of 4 bytes, deltas).
  slow (~1 Hz): GPU PCIe traffic (rsmi_dev_pci_throughput_get blocks ~1 s per call, so all GPUs
      are sampled in parallel threads). The header documents bytes per second; amdgpu drivers
      report packet counts that must be multiplied by max_pkt_sz. Both readings are kept and the
      chosen interpretation is labelled until a known-size transfer calibrates it.
`hy-smi` is not used: one call takes ~8 s.

Every failure degrades to None plus an entry in `errors`; a probe never raises on missing
hardware, libraries or permissions.
"""
import ctypes
import glob
import json
import os
import socket
import threading
import time

SMI_PATHS = ("/opt/hyhal/lib/librocm_smi64.so", "/opt/rocm/lib/librocm_smi64.so", "librocm_smi64.so")
PCIE_UNITS = "packets_x_max_pkt_unverified"


def _read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def _int(path, default=None):
    text = _read(path)
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


class Smi:
    """Minimal ctypes binding of the ROCm/HYHAL SMI library."""

    def __init__(self, paths=SMI_PATHS):
        self.lib, self.error = None, None
        for path in paths:
            try:
                lib = ctypes.CDLL(path)
                if lib.rsmi_init(ctypes.c_uint64(0)) == 0:
                    self.lib, self.path = lib, path
                    break
                self.error = f"rsmi_init failed: {path}"
            except OSError as e:
                self.error = str(e)

    def count(self):
        n = ctypes.c_uint32(0)
        return n.value if self.lib and self.lib.rsmi_num_monitor_devices(ctypes.byref(n)) == 0 else 0

    def _u64(self, fn, *args):
        v = ctypes.c_uint64(0)
        return v.value if getattr(self.lib, fn)(*args, ctypes.byref(v)) == 0 else None

    def vram_used(self, i):
        return self._u64("rsmi_dev_memory_usage_get", ctypes.c_uint32(i), ctypes.c_int(0))

    def vram_total(self, i):
        return self._u64("rsmi_dev_memory_total_get", ctypes.c_uint32(i), ctypes.c_int(0))

    def bdf(self, i):
        v = self._u64("rsmi_dev_pci_id_get", ctypes.c_uint32(i))
        if v is None:
            return None
        return "%04x:%02x:%02x.%x" % ((v >> 32) & 0xffff, (v >> 8) & 0xff, (v >> 3) & 0x1f, v & 0x7)

    def numa(self, i):
        v = ctypes.c_int32(-1)
        return v.value if self.lib.rsmi_topo_numa_affinity_get(ctypes.c_uint32(i), ctypes.byref(v)) == 0 else None

    def pcie(self, i):
        """(sent, received, max_pkt_sz) over ~1 s; blocks for the sampling window."""
        s, r, m = ctypes.c_uint64(0), ctypes.c_uint64(0), ctypes.c_uint64(0)
        rc = self.lib.rsmi_dev_pci_throughput_get(ctypes.c_uint32(i), ctypes.byref(s), ctypes.byref(r), ctypes.byref(m))
        return (s.value, r.value, m.value) if rc == 0 else None


def _numa_meminfo(node):
    out = {}
    for line in (_read(f"/sys/devices/system/node/node{node}/meminfo", "") or "").splitlines():
        parts = line.split()        # "Node 0 MemFree:  123 kB"
        if len(parts) >= 4:
            out[parts[2].rstrip(":")] = int(parts[3]) * (1024 if parts[-1] == "kB" else 1)
    return out


def _diskstats():
    out = {}
    for line in (_read("/proc/diskstats", "") or "").splitlines():
        f = line.split()
        if len(f) >= 14:
            # sectors are always 512 bytes in diskstats; io_ticks in ms
            out[f[2]] = (int(f[5]) * 512, int(f[9]) * 512, int(f[12]))
    return out


def _mounts_by_disk():
    disks = {}
    for line in (_read("/proc/mounts", "") or "").splitlines():
        dev, path = line.split()[:2]
        if not dev.startswith("/dev/"):
            continue
        part = os.path.basename(dev)
        parent = os.path.basename(os.path.dirname(os.path.realpath(f"/sys/class/block/{part}")))
        disk = parent if parent != "block" else part
        disks.setdefault(disk, [])
        if path not in disks[disk]:
            disks[disk].append(path)
    return disks


class NodeProbe:
    def __init__(self, node=None, smi_paths=SMI_PATHS):
        self.node = node or socket.gethostname()
        self.errors = []
        self.smi = Smi(smi_paths)
        if self.smi.lib is None:
            self.errors.append(f"smi unavailable: {self.smi.error}")
        self._prev = None           # (t, diskstats, ib counters) for rate deltas
        self._inv = None

    # ---- static inventory -------------------------------------------------------------------
    def inventory(self):
        if self._inv is not None:
            return self._inv
        gpus = []
        for i in range(self.smi.count()):
            gpus.append({"index": i, "bdf": self.smi.bdf(i), "numa": self.smi.numa(i),
                         "hbm_total": self.smi.vram_total(i)})
        numa = []
        for path in sorted(glob.glob("/sys/devices/system/node/node[0-9]*"), key=lambda p: int(p.rsplit("node", 1)[1])):
            n = int(path.rsplit("node", 1)[1])
            numa.append({"id": n, "total": _numa_meminfo(n).get("MemTotal"),
                         "cpus": _read(f"{path}/cpulist")})
        mounts = _mounts_by_disk()
        ssds = []
        for path in sorted(glob.glob("/sys/block/nvme*n*")):
            name = os.path.basename(path)
            ssds.append({"name": name, "size": (_int(f"{path}/size", 0) or 0) * 512,
                         "numa": _int(f"{path}/device/device/numa_node", _int(f"{path}/device/numa_node")),
                         "mounts": mounts.get(name, [])})
        nics = []
        for path in sorted(glob.glob("/sys/class/infiniband/*")):
            rate = _read(f"{path}/ports/1/rate", "")
            try:
                gbps = float(rate.split()[0]) / 8
            except (IndexError, ValueError):
                gbps = None
            nics.append({"name": os.path.basename(path), "numa": _int(f"{path}/device/numa_node"),
                         "rate_GBps": gbps, "state": _read(f"{path}/ports/1/state")})
        self._inv = {"node": self.node, "hostname": socket.gethostname(), "taken_at": time.time(),
                     "smi": getattr(self.smi, "path", None), "gpus": gpus, "numa": numa,
                     "ssds": ssds, "nics": nics, "errors": list(self.errors)}
        return self._inv

    # ---- fast state -------------------------------------------------------------------------
    def _ib_counters(self):
        out = {}
        for nic in self.inventory()["nics"]:
            base = f"/sys/class/infiniband/{nic['name']}/ports/1/counters"
            tx, rx, wait = (_int(f"{base}/{k}") for k in ("port_xmit_data", "port_rcv_data", "port_xmit_wait"))
            out[nic["name"]] = (None if tx is None else tx * 4, None if rx is None else rx * 4, wait)
        return out

    def sample_fast(self):
        t0 = time.time()
        inv = self.inventory()
        hbm = {g["index"]: self.smi.vram_used(g["index"]) for g in inv["gpus"]}
        dram = {}
        for n in inv["numa"]:
            m = _numa_meminfo(n["id"])
            # Page cache is reclaimable but shmem (memfd, pinned shared buffers) and dirty pages
            # are not; the graph decides how much of `reclaimable` it is willing to take.
            reclaim = None if m.get("FilePages") is None else max(
                0, m["FilePages"] - m.get("Shmem", 0) - m.get("Dirty", 0) - m.get("Writeback", 0))
            dram[n["id"]] = {"free": m.get("MemFree"), "reclaimable": reclaim,
                             "inactive_file": m.get("Inactive(file)"), "shmem": m.get("Shmem"),
                             "total": m.get("MemTotal")}
        disk, ib, now = _diskstats(), self._ib_counters(), time.time()
        ssd, nic = {}, {}
        prev = self._prev
        dt = None if prev is None else now - prev[0]
        for s in inv["ssds"]:
            free = 0
            for path in s["mounts"]:
                try:
                    st = os.statvfs(path)
                    free += st.f_bavail * st.f_frsize
                except OSError:
                    pass
            row = {"free": free if s["mounts"] else None, "rd_GBps": None, "wr_GBps": None, "util": None}
            cur, old = disk.get(s["name"]), prev and prev[1].get(s["name"])
            if cur and old and dt:
                row.update(rd_GBps=(cur[0] - old[0]) / dt / 1e9, wr_GBps=(cur[1] - old[1]) / dt / 1e9,
                           util=min(1.0, (cur[2] - old[2]) / 1e3 / dt))
            ssd[s["name"]] = row
        for name, cur in ib.items():
            old = prev and prev[2].get(name)
            row = {"tx_GBps": None, "rx_GBps": None, "xmit_wait_per_s": None}
            if old and dt and None not in cur and None not in old:
                row = {"tx_GBps": (cur[0] - old[0]) / dt / 1e9, "rx_GBps": (cur[1] - old[1]) / dt / 1e9,
                       "xmit_wait_per_s": (cur[2] - old[2]) / dt}
            nic[name] = row
        self._prev = (now, disk, ib)
        return {"node": self.node, "t": now, "cost_s": time.time() - t0, "hbm_used": hbm,
                "dram": dram, "ssd": ssd, "nic": nic}

    # ---- slow state -------------------------------------------------------------------------
    def sample_pcie(self):
        """All GPUs in parallel; returns after ~1 s."""
        gpus = [g["index"] for g in self.inventory()["gpus"]]
        raw = {}

        def one(i):
            raw[i] = self.smi.pcie(i)

        t0 = time.time()
        threads = [threading.Thread(target=one, args=(i,)) for i in gpus]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        out = {}
        for i in gpus:
            r = raw.get(i)
            out[i] = None if r is None else {"tx_GBps": r[0] * r[2] / 1e9, "rx_GBps": r[1] * r[2] / 1e9,
                                             "raw": list(r), "units": PCIE_UNITS}
        return {"node": self.node, "t": time.time(), "cost_s": time.time() - t0, "gpu": out}


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--node")
    p.add_argument("--samples", type=int, default=3)
    p.add_argument("--interval", type=float, default=0.2)
    p.add_argument("--pcie", action="store_true")
    a = p.parse_args(argv)
    probe = NodeProbe(a.node)
    t0 = time.time()
    inv = probe.inventory()
    inv_s = time.time() - t0
    fast = []
    for _ in range(a.samples + 1):          # first sample only primes the rate counters
        fast.append(probe.sample_fast())
        time.sleep(a.interval)
    out = {"inventory": inv, "inventory_s": inv_s, "fast": fast[1:],
           "pcie": probe.sample_pcie() if a.pcie else None}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
