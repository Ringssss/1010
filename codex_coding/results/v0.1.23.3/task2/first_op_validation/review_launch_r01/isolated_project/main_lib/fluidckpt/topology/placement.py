"""Choose explicit NUMA owners from observed capacity, preserving feasible locality."""


def choose_numa_owners(probes, requirements, reserve_bytes=2 << 30):
    """Reserve all feasible local owners first, then place remaining allocations nearby.

    This is a preflight choice, not an OS memory reservation. HostAllocation must
    still recheck capacity and verify page placement when the allocation happens.
    """
    remaining, local, distances = {}, {}, {}
    for node, probe in probes.items():
        topo = probe["topology"]
        for row in topo["nodes"]:
            key = node, row["node"]
            mem = row["memory_bytes"]
            remaining[key] = max(0, mem.get("MemFree", 0)+mem.get("FilePages", 0)-mem.get("Shmem", 0)-reserve_bytes)
            distances[key] = [int(x) for x in row["distance"].split()]
        for gpu in topo["gpus"]:
            local[node, gpu["logical_gpu"]] = gpu["numa_node"]
    owners, audit = {}, []
    ordered = sorted(requirements, key=lambda r: -r["nbytes"])
    if len({(r["node"], r["gpu"]) for r in ordered}) != len(ordered):
        raise ValueError("duplicate worker memory requirement")

    def assign(row, numa):
        node, gpu, n = row["node"], row["gpu"], row["nbytes"]
        before = remaining[node, numa]
        remaining[node, numa] -= n
        owners[f"{node}:{gpu}"] = numa
        audit.append(dict(row, gpu_local_numa=local[node, gpu], selected_numa=numa,
                          usable_before_bytes=before, usable_after_bytes=remaining[node, numa],
                          reason="local" if numa == local[node, gpu] else "local_capacity_insufficient"))

    deferred = []
    for row in ordered:
        if type(row["nbytes"]) is not int or row["nbytes"] < 0:
            raise ValueError("nonnegative integer memory requirement required")
        near = local[row["node"], row["gpu"]]
        if remaining[row["node"], near] >= row["nbytes"]: assign(row, near)
        else: deferred.append(row)
    for row in deferred:
        node, gpu = row["node"], row["gpu"]
        choices = [n for host, n in remaining if host == node and remaining[host, n] >= row["nbytes"]]
        if not choices: raise MemoryError(f"{node}:{gpu}: no NUMA node fits {row['nbytes']} bytes plus reserve")
        near = local[node, gpu]
        choice = min(choices, key=lambda n: (distances[node, near][n], -remaining[node, n], n))
        assign(row, choice)
    return {"owners": owners, "audit": audit, "reserve_bytes_per_numa": reserve_bytes,
            "scope": "observed headroom minus explicit allocations; revalidated at allocation time"}
