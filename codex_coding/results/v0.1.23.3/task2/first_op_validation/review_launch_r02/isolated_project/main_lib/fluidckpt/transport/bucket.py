"""Bucket planning: turn a tensor inventory into a small number of large transfer units.

A checkpoint is a *many-small-tensors* problem disguised as a bytes problem. GLM-5.3: 118,629
tensors, median 12 KiB, but 97.9% of the bytes sit in 58,776 tensors of 8-16 MiB. The fp8
`weight_scale_inv` scales (58,776 of them, 4-8 KiB) are half the tensor count and none of the
payload.

Bucketing trades a little padding and a little extra host-side staging for two orders of
magnitude fewer collective handshakes. The planner is a first-fit-decreasing bin pack over a
target bucket size, with an optional not-before grouping key (`layer`, so a bucket stays inside
one layer and a failure/retry is scoped).
"""
from __future__ import annotations

import dataclasses
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

MiB = 1 << 20


@dataclasses.dataclass
class Bucket:
    """One transfer unit: the tensors it carries and the total payload bytes."""
    keys: List[str]
    sizes: List[int]          # bytes per tensor, same order as keys
    layer: Optional[int] = None

    @property
    def bytes(self) -> int:
        return sum(self.sizes)

    @property
    def n_tensors(self) -> int:
        return len(self.keys)

    def __repr__(self) -> str:
        return (f"Bucket(layer={self.layer}, n={self.n_tensors}, "
                f"{self.bytes/MiB:.1f} MiB)")


def layer_of(key: str) -> Optional[int]:
    """`model.layers.57.mlp.experts.3.gate_proj.weight` -> 57. None for embed/lm_head."""
    parts = key.split(".")
    for i, p in enumerate(parts):
        if p == "layers" and i + 1 < len(parts):
            try:
                return int(parts[i + 1])
            except ValueError:
                return None
    return None


def plan_buckets_from_layout(
    sizes: Dict[str, int],
    target_bytes: int = 128 * MiB,
    group_by_layer: bool = True,
    max_buckets: Optional[int] = None,
) -> List[Bucket]:
    """Bin-pack a {tensor_name: nbytes} inventory into buckets of ~target_bytes.

    Tensors larger than the target get their own bucket -- they are already efficient, and
    splitting them would add a copy for no handshake savings.
    """
    if group_by_layer:
        by_layer: Dict[Optional[int], List[Tuple[str, int]]] = {}
        for k, v in sizes.items():
            by_layer.setdefault(layer_of(k), []).append((k, v))
        groups: Iterable[List[Tuple[str, int]]] = by_layer.values()
    else:
        groups = [list(sizes.items())]

    buckets: List[Bucket] = []
    for items in groups:
        for b in _pack_one_group(items, target_bytes):
            buckets.append(b)

    if max_buckets is not None and len(buckets) > max_buckets:
        buckets = _coalesce(buckets, max_buckets)
    return buckets


def _pack_one_group(items: Sequence[Tuple[str, int]], target: int) -> List[Bucket]:
    """First-fit-decreasing within one layer."""
    items = sorted(items, key=lambda kv: -kv[1])
    open_buckets: List[Bucket] = []
    for k, sz in items:
        if sz >= target:
            buckets_extra = Bucket([k], [sz], layer_of(k))
            open_buckets.append(buckets_extra)
            continue
        placed = False
        for b in open_buckets:
            if b.bytes + sz <= target:
                b.keys.append(k)
                b.sizes.append(sz)
                placed = True
                break
        if not placed:
            open_buckets.append(Bucket([k], [sz], layer_of(k)))
    return open_buckets


def _coalesce(buckets: List[Bucket], n: int) -> List[Bucket]:
    """Merge the smallest buckets until at most n remain (ignores layer boundaries)."""
    buckets = sorted(buckets, key=lambda b: b.bytes)
    while len(buckets) > n:
        a = buckets.pop(0)
        c = buckets.pop(0)
        merged = Bucket(a.keys + c.keys, a.sizes + c.sizes, a.layer if a.layer == c.layer else None)
        buckets.append(merged)
        buckets.sort(key=lambda b: b.bytes)
    return buckets


def plan_buckets(tensors: Sequence[Tuple[str, int]], **kw) -> List[Bucket]:
    """Convenience wrapper over plan_buckets_from_layout for a sequence of (name, nbytes)."""
    return plan_buckets_from_layout(dict(tensors), **kw)


def summarize(buckets: Sequence[Bucket], total_bytes: Optional[int] = None) -> str:
    n_t = sum(b.n_tensors for b in buckets)
    n_b = len(buckets)
    byt = sum(b.bytes for b in buckets)
    biggest = max(b.bytes for b in buckets) if buckets else 0
    med = sorted(b.bytes for b in buckets)[len(buckets) // 2] if buckets else 0
    s = (f"{n_t} tensors -> {n_b} buckets ({n_b/max(n_t,1)*100:.2f}% of per-tensor count); "
         f"{byt/MiB:.0f} MiB total, median bucket {med/MiB:.1f} MiB, max {biggest/MiB:.1f} MiB")
    if total_bytes:
        s += f"; padding-free (bucket bytes == payload: {byt == total_bytes})"
    return s
