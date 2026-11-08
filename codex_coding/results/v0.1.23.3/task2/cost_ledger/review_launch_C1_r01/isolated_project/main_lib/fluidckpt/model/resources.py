"""Shared-resource model: which physical resources a flow consumes, and its single-stream cap.

A *variant* selects which resource families exist, so model structure can be compared
on held-out data (e.g. "additive spec sheet" vs "per-GPU links + NUMA + socket").
"""
import statistics

SOCKET = {n: 0 if n < 4 else 1 for n in range(8)}  # numactl -H: nodes 0-3 / 4-7 are the two sockets

VARIANTS = {
    "V0_additive": set(),
    "V1_links": {"link", "nvme"},
    "V2_links_numa": {"link", "nvme", "numa"},
    "V3_links_numa_socket": {"link", "nvme", "numa", "socket"},
    "V4_full_host": {"link", "nvme", "numa", "socket", "host"},
    "V5_full_unified_ports": {"link", "nvme", "numa", "socket", "host", "unified_ports"},
    # after profile #2: memory-node read+write budget, and the remote-bidirectional conflict rule
    "V6_links_numa_rw": {"link", "nvme", "numa", "rw"},
    "V7_links_numa_rw_conflict": {"link", "nvme", "numa", "rw", "conflict"},
    "V8_links_numa_rw_socket_conflict": {"link", "nvme", "numa", "rw", "socket", "conflict"},
    # P2P send+receive on one GPU share a budget (bidirectional pair 143+143, ring 141 per hop)
    "V9_links_hswio_numa_rw_conflict": {"link", "hsw_io", "nvme", "numa", "rw", "conflict"},
}

CONFLICT = "bidir_remote"  # pseudo-resource: per-flow cap when a GPU mixes host-in and host-out, one remote


def key(spec):
    k = spec["kind"]
    if k == "h2d":
        return ("h2d", spec["numa"], spec["gpu"])
    if k == "d2h":
        return ("d2h", spec["gpu"], spec["numa"])
    if k == "p2p":
        return ("p2p", spec["src"], spec["dst"])
    if k == "nvme":
        return ("nvme", spec["numa"])
    if k == "nvme2gpu":
        return ("nvme2gpu", spec["numa"], spec["gpu"])
    raise ValueError(k)


def resources(spec, fam, gpu_numa):
    """Resource names a flow consumes under the given resource families."""
    k, r = spec["kind"], []
    uni = "unified_ports" in fam
    if k in ("h2d", "nvme2gpu"):
        n, g = spec["numa"], spec["gpu"]
        if "link" in fam: r.append(f"in:{g}" if uni else f"pcie_in:{g}")
        if "numa" in fam: r.append(f"mem_rd:{n}")
        if "rw" in fam: r.append(f"mem_rw:{n}")
        if "socket" in fam and SOCKET[n] != SOCKET[gpu_numa[g]]: r.append(f"xsock:{SOCKET[n]}>{SOCKET[gpu_numa[g]]}")
        if "host" in fam: r.append("host_dma")
        if k == "nvme2gpu":
            if "nvme" in fam: r.append("nvme_rd")
            if "numa" in fam: r.append(f"mem_wr:{n}")
    elif k == "d2h":
        g, n = spec["gpu"], spec["numa"]
        if "link" in fam: r.append(f"out:{g}" if uni else f"pcie_out:{g}")
        if "numa" in fam: r.append(f"mem_wr:{n}")
        if "rw" in fam: r.append(f"mem_rw:{n}")
        if "socket" in fam and SOCKET[n] != SOCKET[gpu_numa[g]]: r.append(f"xsock:{SOCKET[gpu_numa[g]]}>{SOCKET[n]}")
        if "host" in fam: r.append("host_dma")
    elif k == "p2p":
        i, j = spec["src"], spec["dst"]
        if "link" in fam:
            r.append(f"out:{i}" if uni else f"hsw_out:{i}")
            r.append(f"in:{j}" if uni else f"hsw_in:{j}")
        if "hsw_io" in fam:
            r += [f"hsw_io:{i}", f"hsw_io:{j}"]
    elif k == "nvme":
        n = spec["numa"]
        if "nvme" in fam: r.append("nvme_rd")
        if "numa" in fam: r.append(f"mem_wr:{n}")
        if "rw" in fam: r.append(f"mem_rw:{n}")
        if "socket" in fam and SOCKET[n] != SOCKET[0]: r.append(f"xsock:0>{SOCKET[n]}")  # NVMe sits on NUMA 0
        if "host" in fam: r.append("host_dma")
    return r


def conflicted(specs, gpu_numa):
    """Indices of host flows on GPUs that have both host-inbound and host-outbound traffic where at
    least one side is not the GPU's own NUMA node (measured: both directions fall to ~7 GB/s)."""
    by_gpu = {}
    for i, s in enumerate(specs):
        if s["kind"] in ("h2d", "nvme2gpu", "d2h"):
            d = "out" if s["kind"] == "d2h" else "in"
            by_gpu.setdefault(s["gpu"], []).append((i, d, s["numa"] != gpu_numa[s["gpu"]]))
    out = set()
    for g, fl in by_gpu.items():
        if {d for _, d, _ in fl} == {"in", "out"} and any(rem for _, _, rem in fl):
            out.update(i for i, _, _ in fl)
    return out


class SingleCaps:
    """Single-stream bandwidth per path, from the uncontended measurements."""

    def __init__(self, profile):
        self.caps = {}
        for row in profile["single"]:
            self.caps[key(row["spec"])] = statistics.median(row["GBps"])
        conc = profile["concurrent"]
        for name in ("nvme_numa0_k1",):
            if name in conc:
                self.caps[("nvme", "*")] = statistics.median(r[0]["GBps"] for r in conc[name]["runs"])

    def cap(self, spec):
        k = key(spec)
        if k[0] == "nvme":
            return self.caps.get(k, self.caps[("nvme", "*")])
        if k[0] == "nvme2gpu":
            return min(self.caps[("nvme", "*")], self.caps[("h2d", k[1], k[2])])
        return self.caps[k]
