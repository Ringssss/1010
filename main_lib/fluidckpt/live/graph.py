"""Live multi-node resource graph: slow topology, fast state, immutable snapshots.

Topology (rebuilt only when membership changes):
  vertices  <node>/hbm/<gpu>, <node>/dram/<numa>, <node>/ssd/<disk> on every node;
  edges     h2d/d2h (DRAM NUMA <-> GPU), p2p (GPU <-> GPU, same node), nvme_rd/nvme_wr (SSD <-> DRAM),
            ib (GPU <-> GPU and DRAM <-> DRAM across nodes, through each side's nearest HCA);
  resources every shared capacity an edge consumes. Contention is expressed only through shared
            resources, so the planner needs no special cases.
Paths between two vertices are enumerated once per topology version and cached: state updates
(tens per second) never trigger enumeration.

State (refreshed from probe samples): per-vertex free bytes for us and per-resource background
load. Background = observed - our own registered in-flight traffic, smoothed by an EWMA so one
noisy sample does not trigger replanning. Unobserved resources (NUMA memory bandwidth has no
cheap counter) keep their nominal capacity; stale observations are derated by Snapshot.

Nominal capacities (GB/s) come from codex_coding/results/profile/profile_node27.json (single and
concurrent medians), extrapolated to the identical peer nodes, and transport/flowplan.py for IB.
Values without a measurement are marked "assumed".
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .types import Edge, Path, Resource, ResourceState, Snapshot, Topology, Vertex, VertexState

GiB = 1 << 30

# (GB/s, provenance)
NOMINAL = {
    "h2d_stream": (46.0, "profile_node27 single h2d median"),
    "d2h_stream": (46.0, "profile_node27 single d2h median"),
    "pcie_in": (47.5, "profile_node27 h2d_local_k8 per-GPU"),
    "pcie_out": (46.5, "profile_node27 d2h_local_k8 per-GPU"),
    "memrd": (95.0, "profile_node27 h2d_numa0_to_k8 aggregate"),
    "memwr": (95.0, "assumed = memrd"),
    "p2p_stream": (156.0, "profile_node27 single p2p median"),
    "hsw_in": (165.0, "profile_node27 p2p_fanin_to0_k7 aggregate"),
    "hsw_out": (165.0, "assumed = hsw_in"),
    "nvme_stream": (5.8, "profile_node27 nvme_numa0_k1"),
    "nvme_rd": (7.0, "profile_node27 nvme_numa0_k4 aggregate"),
    "nvme_wr": (3.0, "assumed"),
    "ib_stream": (48.5, "flowplan pair stream node27<->node29"),
    "ib_nic": (50.0, "assumed = 400 Gb/s link rate"),
    "ib_node": (205.8, "flowplan ib_node aggregate, 8 pair streams"),
    "ib_host_stream": (25.0, "assumed: host-to-host RDMA without GPU staging"),
}


@dataclass
class Policy:
    hbm_headroom: int = 2 * GiB          # left for the GPU's tenant (allocator slack, KV growth)
    dram_headroom_frac: float = 0.10     # of the NUMA node's total
    dram_reclaim_frac: float = 0.5       # share of reclaimable page cache we are willing to take
    ssd_headroom_frac: float = 0.10
    ssd_mount_deny: tuple = ("/", "/boot", "/boot/efi")   # system disks never hold chunks
    stale_fast_s: float = 2.0            # fast samples arrive at ~10 Hz
    stale_pcie_s: float = 5.0            # PCIe samples arrive at ~1 Hz
    ewma: float = 0.5                    # weight of the newest background sample


def _socket(numa):
    return 0 if numa is None or numa < 4 else 1


def _nearest_nic(nics, numa):
    if not nics:
        return None
    same = [n for n in nics if n["numa"] == numa]
    if same:
        return same[0]["name"]
    sock = [n for n in nics if _socket(n["numa"]) == _socket(numa)]
    pool = sock or nics
    return pool[(numa or 0) % len(pool)]["name"]


def build_topology(inventories, policy=None, version=0):
    """inventories: probe.NodeProbe.inventory() dicts, one per node (node name = inventory['node'])."""
    policy = policy or Policy()
    V, E, R = {}, [], {}

    def res(rid, key):
        if rid not in R:
            gbps, src = NOMINAL[key]
            R[rid] = Resource(rid, gbps, src)
        return rid

    gpu_nic, dram_nic = {}, {}
    for inv in inventories:
        n = inv["node"]
        nics = [x for x in inv["nics"] if x.get("state", "").endswith("ACTIVE")]
        for g in inv["gpus"]:
            vid = f"{n}/hbm/{g['index']}"
            V[vid] = Vertex(vid, n, "hbm", str(g["index"]), g["hbm_total"] or 0, g["numa"])
            gpu_nic[vid] = _nearest_nic(nics, g["numa"])
        for m in inv["numa"]:
            vid = f"{n}/dram/{m['id']}"
            V[vid] = Vertex(vid, n, "dram", str(m["id"]), m["total"] or 0, m["id"])
            dram_nic[vid] = _nearest_nic(nics, m["id"])
        for s in inv["ssds"]:
            usable = [p for p in s["mounts"] if p not in policy.ssd_mount_deny]
            if usable and not any(p in policy.ssd_mount_deny for p in s["mounts"]):
                vid = f"{n}/ssd/{s['name']}"
                V[vid] = Vertex(vid, n, "ssd", s["name"], s["size"], s["numa"])

        hbms = [v for v in V.values() if v.node == n and v.kind == "hbm"]
        drams = [v for v in V.values() if v.node == n and v.kind == "dram"]
        ssds = [v for v in V.values() if v.node == n and v.kind == "ssd"]
        for g in hbms:
            for d in drams:
                E.append(Edge(d.id, g.id, "h2d", (res(f"{n}/memrd/{d.index}", "memrd"),
                                                  res(f"{n}/pcie_in/{g.index}", "pcie_in")), NOMINAL["h2d_stream"][0]))
                E.append(Edge(g.id, d.id, "d2h", (res(f"{n}/pcie_out/{g.index}", "pcie_out"),
                                                  res(f"{n}/memwr/{d.index}", "memwr")), NOMINAL["d2h_stream"][0]))
            for h in hbms:
                if h.id != g.id:
                    E.append(Edge(g.id, h.id, "p2p", (res(f"{n}/hsw_out/{g.index}", "hsw_out"),
                                                      res(f"{n}/hsw_in/{h.index}", "hsw_in")), NOMINAL["p2p_stream"][0]))
        for s in ssds:
            for d in drams:
                E.append(Edge(s.id, d.id, "nvme_rd", (res(f"{n}/nvme_rd/{s.index}", "nvme_rd"),
                                                      res(f"{n}/memwr/{d.index}", "memwr")), NOMINAL["nvme_stream"][0]))
                E.append(Edge(d.id, s.id, "nvme_wr", (res(f"{n}/memrd/{d.index}", "memrd"),
                                                      res(f"{n}/nvme_wr/{s.index}", "nvme_wr")), NOMINAL["nvme_stream"][0]))

    # Cross-node: GPU<->GPU (GPU-direct, what the keeper data plane does) and host<->host.
    nodes = sorted({v.node for v in V.values()})
    for a in nodes:
        for b in nodes:
            if a == b:
                continue
            for kind, nic_of, side_out, side_in, stream in (
                    ("hbm", gpu_nic, "pcie_out", "pcie_in", "ib_stream"),
                    ("dram", dram_nic, "memrd", "memwr", "ib_host_stream")):
                src = [v for v in V.values() if v.node == a and v.kind == kind]
                dst = [v for v in V.values() if v.node == b and v.kind == kind]
                for s in src:
                    for d in dst:
                        ns, nd = nic_of[s.id], nic_of[d.id]
                        if ns is None or nd is None:
                            continue
                        E.append(Edge(s.id, d.id, "ib", (
                            res(f"{a}/{side_out}/{s.index}", side_out), res(f"{a}/ib_tx/{ns}", "ib_nic"),
                            res(f"{a}/ib_node_tx", "ib_node"), res(f"{b}/ib_rx/{nd}", "ib_nic"),
                            res(f"{b}/ib_node_rx", "ib_node"), res(f"{b}/{side_in}/{d.index}", side_in)),
                            NOMINAL[stream][0]))
    return Topology(V, E, R, version)


class LiveGraph:
    def __init__(self, topology, policy=None):
        self.policy = policy or Policy()
        self.topology = topology
        self._out, self._in, self._pair = {}, {}, {}
        for e in topology.edges:
            self._out.setdefault(e.src, []).append(e)
            self._in.setdefault(e.dst, []).append(e)
            self._pair.setdefault((e.src, e.dst), []).append(e)
        self._paths = {}                 # (src, dst, max_hops, k) -> [Path]; per topology version
        self._vstate, self._rstate = {}, {}
        self._own_rate, self._own_bytes = {}, {}
        self._seen = {}                  # node -> (fast_t, pcie_t)
        self._version, self._snap = 0, None

    # ---- our own footprint (so it is not mistaken for another tenant) ------------------------
    def own_traffic(self, rates):
        """rates: {resource_id: GB/s} currently driven by our transfers (replaces previous)."""
        self._own_rate = dict(rates)

    def own_bytes(self, vertex_id, nbytes):
        self._own_bytes[vertex_id] = nbytes

    # ---- state ingestion ---------------------------------------------------------------------
    def _bg(self, rid, observed, t):
        if rid not in self.topology.resources or observed is None:
            return
        bg = max(0.0, observed - self._own_rate.get(rid, 0.0))
        old = self._rstate.get(rid)
        if old is not None:
            bg = self.policy.ewma * bg + (1 - self.policy.ewma) * old.background_GBps
        self._rstate[rid] = ResourceState(bg, t)

    def ingest(self, node, fast=None, pcie=None):
        p, t = self.policy, time.time()
        if fast:
            t = fast["t"]
            for g, used in fast["hbm_used"].items():
                vid = f"{node}/hbm/{g}"
                v = self.topology.vertices.get(vid)
                if v is not None and used is not None:
                    self._vstate[vid] = VertexState(max(0, v.capacity_bytes - used - p.hbm_headroom), t)
            for k, m in fast["dram"].items():
                vid = f"{node}/dram/{k}"
                v = self.topology.vertices.get(vid)
                if v is not None and m.get("free") is not None:
                    free = m["free"] + p.dram_reclaim_frac * (m.get("reclaimable") or 0)
                    self._vstate[vid] = VertexState(max(0, int(free - p.dram_headroom_frac * v.capacity_bytes)), t)
            for name, s in fast["ssd"].items():
                vid = f"{node}/ssd/{name}"
                v = self.topology.vertices.get(vid)
                if v is not None and s.get("free") is not None:
                    self._vstate[vid] = VertexState(max(0, int(s["free"] - p.ssd_headroom_frac * v.capacity_bytes)), t)
                if s.get("rd_GBps") is not None:
                    nominal = NOMINAL["nvme_rd"][0]
                    # A device busy with small random IO moves few bytes but has no headroom left.
                    self._bg(f"{node}/nvme_rd/{name}", max(s["rd_GBps"] + s["wr_GBps"], (s["util"] or 0) * nominal), t)
                    self._bg(f"{node}/nvme_wr/{name}", s["wr_GBps"] + s["rd_GBps"], t)
            tx_all = rx_all = 0.0
            for name, s in fast["nic"].items():
                if s.get("tx_GBps") is None:
                    continue
                self._bg(f"{node}/ib_tx/{name}", s["tx_GBps"], t)
                self._bg(f"{node}/ib_rx/{name}", s["rx_GBps"], t)
                tx_all, rx_all = tx_all + s["tx_GBps"], rx_all + s["rx_GBps"]
            self._bg(f"{node}/ib_node_tx", tx_all, t)
            self._bg(f"{node}/ib_node_rx", rx_all, t)
        if pcie:
            t = pcie["t"]
            for g, s in pcie["gpu"].items():
                if s is not None:
                    self._bg(f"{node}/pcie_in/{g}", s["rx_GBps"], t)
                    self._bg(f"{node}/pcie_out/{g}", s["tx_GBps"], t)
        seen = self._seen.get(node, (None, None))
        self._seen[node] = (fast["t"] if fast else seen[0], pcie["t"] if pcie else seen[1])
        self._version += 1
        self._snap = None

    # ---- snapshots ---------------------------------------------------------------------------
    def snapshot(self, now=None):
        if self._snap is not None and now is None:
            return self._snap
        now = now or time.time()
        p = self.policy
        vs = {}
        for vid, s in self._vstate.items():
            vs[vid] = VertexState(s.free_bytes, s.observed_at, now - s.observed_at > p.stale_fast_s)
        rs = {}
        for rid, s in self._rstate.items():
            bound = p.stale_pcie_s if ("/pcie_" in rid) else p.stale_fast_s
            rs[rid] = ResourceState(s.background_GBps, s.observed_at, now - s.observed_at > bound)
        self._snap = Snapshot(self.topology, vs, rs, self._version, now)
        return self._snap

    def changed_since(self, snap, rel=0.2):
        """Resources/vertices whose usable capacity moved by more than `rel` since `snap`."""
        cur, out = self.snapshot(), []
        for rid, r in self.topology.resources.items():
            a, b = snap.residual_GBps(rid), cur.residual_GBps(rid)
            if abs(a - b) > rel * max(a, 0.05 * r.nominal_GBps):
                out.append(rid)
        for vid in self.topology.vertices:
            a, b = snap.vertices.get(vid), cur.vertices.get(vid)
            if (a is None) != (b is None) or (a and abs(a.free_bytes - b.free_bytes) > rel * max(a.free_bytes, GiB)):
                out.append(vid)
        return out

    # ---- paths -------------------------------------------------------------------------------
    def paths(self, src, dst, max_hops=3, k=8):
        """Up to k simple paths src -> dst, best nominal bottleneck first. Cached per topology.

        Grammar: SSD only as an endpoint; never detour through a third node (the single
        cross-node hop, if any, lands on the destination's node); never revisit a vertex.
        """
        key = (src, dst, max_hops, k)
        hit = self._paths.get(key)
        if hit is not None:
            return hit
        V, R = self.topology.vertices, self.topology.resources
        target = V[dst].node
        found = []

        def ok(path_vertices):
            # vertices along the path: no repeats, SSD only at the ends, no third node
            if len(set(path_vertices)) != len(path_vertices):
                return False
            for i, vid in enumerate(path_vertices):
                w = V[vid]
                if 0 < i < len(path_vertices) - 1 and w.kind == "ssd":
                    return False
                if i and w.node != V[path_vertices[i - 1]].node and w.node != target:
                    return False
            return True

        # Meet in the middle: first edge x last edge, joined by a dict lookup. O(deg_out * deg_in)
        # per pair instead of a depth-3 DFS over every neighbour's neighbourhood.
        for e in self._pair.get((src, dst), ()):
            found.append(Path((e,)))
        if max_hops >= 2:
            for e1 in self._out.get(src, ()):
                if ok((src, e1.dst, dst)):
                    for e2 in self._pair.get((e1.dst, dst), ()):
                        found.append(Path((e1, e2)))
        if max_hops >= 3:
            for e1 in self._out.get(src, ()):
                if e1.dst == dst:
                    continue
                for e3 in self._in.get(dst, ()):
                    if e3.src in (src, e1.dst) or not ok((src, e1.dst, e3.src, dst)):
                        continue
                    for e2 in self._pair.get((e1.dst, e3.src), ()):
                        found.append(Path((e1, e2, e3)))

        def score(p):
            cap = min([p.single_stream_GBps] + [R[r].nominal_GBps for r in p.resources])
            return (-cap, len(p.edges))
        # A path whose resources include all of another path's resources can never do better:
        # drop it (e.g. h2d+d2h+h2d round trips, p2p chains through two extra GPUs).
        kept, undominated = [], []
        for p in sorted(found, key=lambda p: (len(p.resources), score(p))):
            rs = set(p.resources)
            if not any(q <= rs for q in kept):
                kept.append(rs)
                undominated.append(p)
        # Paths are chosen before any state is known, so keep them diverse: take the best path of
        # each shape (h2d, h2d+p2p, h2d+ib, ...) in turn, so a busy intermediate never removes a
        # whole class of alternatives.
        groups = {}
        for p in sorted(undominated, key=score):
            groups.setdefault(tuple(e.kind for e in p.edges), []).append(p)
        order = sorted(groups.values(), key=lambda g: score(g[0]))
        chosen = []
        while len(chosen) < k and any(order):
            for g in order:
                if g and len(chosen) < k:
                    chosen.append(g.pop(0))
        self._paths[key] = chosen
        return chosen
