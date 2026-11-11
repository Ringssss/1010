"""Shared contracts for the live resource graph, chunk store and flow planner.

The graph is split in two layers so it can stay real-time on a multi-node cluster:

  * Topology (slow): vertices (storage media), edges (links) and the shared resources each edge
    consumes. Built once per cluster membership change; candidate paths are cached on it.
  * State (fast): per-vertex free bytes and per-resource background load, refreshed by the
    per-node probe agents. A Snapshot freezes one version of the state for a planner call.

Naming. Vertex ids are "<node>/<kind>/<index>", e.g. "n27/hbm/4", "n27/dram/3" (NUMA node 3),
"n28/ssd/nvme0n1". Resource ids are free-form but conventionally "<node>/<family>/<index>",
e.g. "n27/pcie_in/4", "n27/memrd/3", "n27/nvme_rd/nvme0n1", "n27/ib_tx/shca_0".
Bandwidths are GB/s (1e9 bytes/s); capacities are bytes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

TIERS = ("hbm", "dram", "ssd")


@dataclass(frozen=True)
class Vertex:
    id: str
    node: str
    kind: str                       # one of TIERS
    index: str                      # GPU ordinal, NUMA node, or block device name
    capacity_bytes: int
    numa: Optional[int] = None      # host NUMA affinity (GPU / NVMe / DRAM bank)


@dataclass(frozen=True)
class Edge:
    """A directed data movement primitive between two vertices.

    `resources` are every shared capacity the movement consumes (link, memory controller,
    device, NIC). Two edges that share a resource contend for it.
    """
    src: str
    dst: str
    kind: str                       # "h2d" | "d2h" | "p2p" | "nvme_rd" | "nvme_wr" | "ib" | ...
    resources: Tuple[str, ...]
    single_stream_GBps: float       # uncontended rate of one stream on this edge


@dataclass(frozen=True)
class Resource:
    id: str
    nominal_GBps: float             # calibrated uncontended capacity
    source: str = "calibrated"      # provenance of the nominal value


@dataclass
class Topology:
    vertices: Dict[str, Vertex]
    edges: List[Edge]
    resources: Dict[str, Resource]
    version: int = 0                # bumped only when membership/links change


@dataclass(frozen=True)
class VertexState:
    free_bytes: int                 # usable by us now, after other tenants and safety headroom
    observed_at: float              # monotonic-ish wall time of the sample (time.time())
    stale: bool = False             # sample older than the agent's staleness bound


@dataclass(frozen=True)
class ResourceState:
    background_GBps: float          # load not attributable to our own transfers
    observed_at: float
    stale: bool = False

    def residual(self, nominal_GBps: float, floor_frac: float = 0.05) -> float:
        # Never plan on zero: a fully busy link still drains slowly; stale data is derated.
        r = max(nominal_GBps - self.background_GBps, nominal_GBps * floor_frac)
        return r * (0.5 if self.stale else 1.0)


@dataclass(frozen=True)
class Snapshot:
    topology: Topology
    vertices: Dict[str, VertexState]
    resources: Dict[str, ResourceState]
    version: int                    # bumped on every state update
    taken_at: float

    def residual_GBps(self, resource_id: str) -> float:
        nominal = self.topology.resources[resource_id].nominal_GBps
        state = self.resources.get(resource_id)
        return nominal if state is None else state.residual(nominal)


@dataclass(frozen=True)
class Path:
    """A route from a storage vertex to a destination vertex: consecutive edges."""
    edges: Tuple[Edge, ...]

    @property
    def src(self) -> str:
        return self.edges[0].src

    @property
    def dst(self) -> str:
        return self.edges[-1].dst

    @property
    def resources(self) -> Tuple[str, ...]:
        seen: List[str] = []
        for e in self.edges:
            seen += [r for r in e.resources if r not in seen]
        return tuple(seen)

    @property
    def single_stream_GBps(self) -> float:
        return min(e.single_stream_GBps for e in self.edges)


@dataclass(frozen=True)
class Chunk:
    """A byte range of one source file; the unit of placement and transfer."""
    id: str                         # sha256 of the bytes (content identity)
    file: str                       # path relative to the model directory
    offset: int
    nbytes: int
    tensors: Tuple[str, ...] = ()   # tensors fully or partially inside this range


@dataclass
class Transfer:
    """One planned movement of one chunk over one path."""
    chunk_id: str
    path: Path
    nbytes: int
    order: int = 0                  # position in the path's queue


@dataclass
class FetchPlan:
    transfers: List[Transfer]
    predicted_s: float              # makespan under the snapshot's residual capacities
    lower_bound_s: float            # fractional max-concurrent-flow optimum
    snapshot_version: int
    planning_s: float = 0.0
    notes: Dict[str, object] = field(default_factory=dict)
