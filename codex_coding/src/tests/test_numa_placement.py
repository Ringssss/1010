"""Preflight capacity must preserve other workers' feasible local reservations."""
import pytest
from fluidckpt.topology.placement import choose_numa_owners


def probe():
    return {"n": {"topology": {
        "gpus": [{"logical_gpu": i, "numa_node": i} for i in range(3)],
        "nodes": [{"node": i, "distance": "10 16 32" if i == 0 else "16 10 16",
                   "memory_bytes": {"MemFree": free, "FilePages": 0, "Shmem": 0}}
                  for i, free in enumerate([10, 50, 50])]}}}


def test_preserves_local_owner_before_using_other_numa():
    result = choose_numa_owners(probe(), [{"node": "n", "gpu": 0, "nbytes": 35},
                                        {"node": "n", "gpu": 1, "nbytes": 35}], reserve_bytes=2)
    assert result["owners"] == {"n:1": 1, "n:0": 2}
    assert result["audit"][-1]["reason"] == "local_capacity_insufficient"


def test_rejects_when_no_node_has_full_contiguous_allocation_capacity():
    with pytest.raises(MemoryError, match="no NUMA"):
        choose_numa_owners(probe(), [{"node": "n", "gpu": 0, "nbytes": 49}], reserve_bytes=2)


def test_shared_local_memory_is_subtracted_and_shmem_is_excluded():
    p = probe(); p["n"]["topology"]["gpus"][0]["numa_node"] = 1
    p["n"]["topology"]["nodes"][2]["memory_bytes"]["Shmem"] = 30
    with pytest.raises(MemoryError):
        choose_numa_owners(p, [{"node": "n", "gpu": 0, "nbytes": 35},
                              {"node": "n", "gpu": 1, "nbytes": 35}], reserve_bytes=2)
