"""Capacity-aware initial host-memory bindings; sampled headroom is rechecked at allocation."""


def choose_block_bindings(topology, demands, block_bytes=256 << 20, guard_bytes=2 << 30):
    """Allow a large shard to use several NUMA nodes without overbooking any of them."""
    nodes = {row['node']: row for row in topology['nodes']}
    near = {row['logical_gpu']: row['numa_node'] for row in topology['gpus']}
    available = {node: max(0, row['memory_bytes'].get('MemFree', 0)
        +row['memory_bytes'].get('FilePages', 0)-row['memory_bytes'].get('Shmem', 0)-guard_bytes)
        for node, row in nodes.items()}
    if block_bytes <= 0 or block_bytes % 4096:
        raise ValueError('block size must be positive and page aligned')
    assignments, deferred = {}, []
    for demand in demands:
        gpu = demand['gpu']
        assignments[gpu] = []
        for off in range(0, demand['nbytes'], block_bytes):
            size = min(block_bytes, demand['nbytes']-off)
            row = {'gpu': gpu, 'off': off, 'n': size, 'allocated_bytes': (size+4095)//4096*4096}
            assignments[gpu].append(row)
            if available[near[gpu]] >= row['allocated_bytes']:
                row['numa'] = near[gpu]
                available[near[gpu]] -= row['allocated_bytes']
            else:
                deferred.append(row)
    for row in deferred:
        choices = [node for node in nodes if available[node] >= row['allocated_bytes']]
        if not choices:
            raise MemoryError('NUMA capacity cannot cover all capture blocks')
        distances = list(map(int, nodes[near[row['gpu']]]['distance'].split()))
        chosen = min(choices, key=lambda node: (distances[node], node))
        row['numa'] = chosen
        available[chosen] -= row['allocated_bytes']
    return {'blocks': assignments, 'remaining_after_guard_bytes': available,
            'guard_bytes_per_numa': guard_bytes,
            'scope': 'capacity-constrained greedy capture binding at independently allocated block granularity'}


def choose_bindings(topology, demands, guard_bytes=2 << 30):
    nodes = {r["node"]: r for r in topology["nodes"]}
    available = {i: max(0, n["memory_bytes"].get("MemFree", 0)+n["memory_bytes"].get("FilePages", 0)
                        -n["memory_bytes"].get("Shmem", 0)) for i, n in nodes.items()}
    initial = dict(available)
    near = {g["logical_gpu"]: g["numa_node"] for g in topology["gpus"]}
    if type(guard_bytes) is not int or guard_bytes < 0: raise ValueError("invalid NUMA guard")
    if len({d["gpu"] for d in demands}) != len(demands): raise ValueError("duplicate GPU memory demand")
    for d in demands:
        if d["gpu"] not in near or near[d["gpu"]] not in nodes or type(d["nbytes"]) is not int or d["nbytes"] <= 0:
            raise ValueError("invalid memory binding demand")
    result, deferred = [], []
    def assign(d, node):
        allocated = (d["nbytes"]+4095)//4096*4096
        result.append({"gpu": d["gpu"], "preferred_numa": near[d["gpu"]], "numa": node,
                       "logical_bytes": d["nbytes"], "allocated_bytes": allocated,
                       "available_before_bytes": available[node], "guard_bytes": guard_bytes})
        available[node] -= allocated
    # Keep feasible near bindings first so a fallback does not unnecessarily displace another GPU.
    for d in demands:
        home_node = near[d["gpu"]]
        if available[home_node] >= (d["nbytes"]+4095)//4096*4096+guard_bytes: assign(d, home_node)
        else: deferred.append(d)
    for d in deferred:
        eligible = [i for i in nodes if available[i] >= (d["nbytes"]+4095)//4096*4096+guard_bytes]
        if not eligible: raise MemoryError(f"no NUMA node can hold {d['nbytes']} bytes for GPU{d['gpu']}")
        distances = list(map(int, nodes[near[d["gpu"]]]["distance"].split()))
        chosen = min(eligible, key=lambda i: (distances[i] if i < len(distances) else float("inf"), i))
        assign(d, chosen)
    return {"bindings": sorted(result, key=lambda d: d["gpu"]), "initial_available_bytes": initial,
            "remaining_available_bytes": available,
            "scope": "greedy initial binding under sampled headroom; not a reservation or globally optimal NUMA assignment"}
