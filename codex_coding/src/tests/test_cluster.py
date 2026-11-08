"""Cluster coverage, budgets, lifecycle and placement invariants without GPU processes."""
import copy
import pytest
from unittest.mock import MagicMock, patch
from fluidckpt.cluster.control import request
from fluidckpt.cluster.catalog import Catalog
from fluidckpt.cluster.planning import check_retirement, placement_plan, restore_plan, validate_plan


PROFILE = {"rates": {"local_dram": 40, "local_hbm": 100, "peer": 80, "network": 20},
           "gpu_ingress_GBps": 100, "gpu_egress_GBps": 100, "dram_GBps": 40, "network_node_GBps": 40}


def test_empty_health_response_is_success():
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b""
    with patch("urllib.request.urlopen", return_value=response):
        assert request("http://localhost:1", "health") == {}


def workers():
    return [{"worker": i, "node": f"node{i//2}", "gpu": i % 2, "numa": i % 2,
             "capabilities": list(PROFILE["rates"]), "budget_bytes": {"dram": 2**30, "hbm": 2**30},
             "used_bytes": {"dram": 0, "hbm": 0}} for i in range(6)]


def replica(rid="r", worker=0, off=0, n=100_000_017, **kw):
    return dict({"id": rid, "worker": worker, "identity": "model0", "tp": 0, "off": off,
                 "n": n, "tier": "dram", "numa": 0, "generation": 1, "state": "VALID", "leases": 0}, **kw)


def target():
    return {"key": "gpu", "worker": 4, "identity": "model0", "tp": 0, "off": 0, "n": 100_000_017}


@pytest.mark.parametrize("strategy", ["nearest", "fastest", "balanced", "resource_lp"])
def test_cluster_partial_and_tail(strategy):
    replicas = [replica(), replica("partial", 2, 17, 40_000_003)]
    p = restore_plan(replicas, [target()], workers(), PROFILE, strategy, quantum=4096)
    validate_plan(p)
    assert sum(o["n"] for o in p["ops"]) == target()["n"]
    for o in p["ops"]:
        r = next(r for r in replicas if r["id"] == o["replica_id"])
        assert o["source_offset"] == o["off"]-r["off"]
        assert o["source_offset"]+o["n"] <= r["n"]


def test_identity_missing_path_and_overlap_rejected():
    with pytest.raises(ValueError): restore_plan([replica(identity="wrong")], [target()], workers(), PROFILE)
    ws = workers(); ws[0]["capabilities"] = []
    with pytest.raises(ValueError): restore_plan([replica()], [target()], ws, PROFILE)
    p = restore_plan([replica()], [target()], workers(), PROFILE)
    p["ops"].append(dict(p["ops"][0], op_id=1))
    with pytest.raises(ValueError): validate_plan(p)


def test_last_coverage_and_leases():
    with pytest.raises(ValueError): check_retirement([replica(), replica("p", off=1, n=100_000_016)], "r")
    assert check_retirement([replica(), replica("copy")], "r")["id"] == "r"
    with pytest.raises(ValueError): check_retirement([replica(leases=1), replica("copy")], "r")


def test_lifecycle_atomic_acquire_and_generation():
    catalog = Catalog({"dram": 8192, "hbm": 8192})
    desc = replica(n=32)
    catalog.reserve(desc, lambda: (object(), None))
    op = {"replica_id": "r", "generation": 1, "identity": "model0", "tp": 0,
          "src_w": 0, "off": 0, "source_offset": 0, "n": 32}
    with pytest.raises(ValueError): catalog.acquire("x", [op])
    catalog.publish("r", True)
    with pytest.raises(ValueError): catalog.acquire("x", [op, dict(op, generation=9)])
    assert catalog.entries["r"]["leases"] == 0
    catalog.acquire("x", [op]); catalog.retire("r")
    assert catalog.entries["r"]["state"] == "RETIRING"
    catalog.release("x")
    assert catalog.used()["dram"] == 0
    catalog.reserve(desc, lambda: (object(), None)); catalog.publish("r", True)
    assert catalog.entries["r"]["generation"] == 2
    with pytest.raises(ValueError): catalog.acquire("y", [op])


def test_budget_checked_before_allocation():
    catalog = Catalog({"dram": 1, "hbm": 1})
    def allocate(): raise AssertionError("allocator must not run")
    with pytest.raises(MemoryError): catalog.reserve(replica(n=32), allocate)


def test_nearest_capacity_and_joint_placement():
    req = dict(replica("new"), preferred_worker=4,
               locations=[{"worker": i, "tier": "dram"} for i in (2, 4)])
    ws = workers()
    assert placement_plan([replica()], [req], ws, PROFILE, extra_budget=1 << 30)["decisions"][0]["replica"]["worker"] == 4
    ws[4]["used_bytes"]["dram"] = 1 << 30
    assert placement_plan([replica()], [req], ws, PROFILE, "capacity", extra_budget=1 << 30)["decisions"][0]["replica"]["worker"] == 2
    result = placement_plan([replica()], [req], workers(), PROFILE, "joint_greedy",
                            scenarios=[{"targets": [target()]}], extra_budget=1 << 30)
    assert result["estimated_after_s"] < result["estimated_before_s"]
    assert result["decisions"][0]["replica"]["worker"] == 4
    with pytest.raises(MemoryError): placement_plan([replica()], [req], workers(), PROFILE, extra_budget=1)


def test_runtime_profile_avoids_ssd_when_serial_target_has_full_dram_coverage():
    from fluidckpt.cluster.control import Cluster
    profile = copy.deepcopy(PROFILE)
    profile['rates']['local_ssd'] = .5
    profile.update(ssd_GBps=.5, store_network_GBps=1.)
    ws = workers()
    for w in ws: w['capabilities'].append('local_ssd')
    size = 35_000_000_017
    demand = dict(target(), n=size)
    replicas = [replica('dram', worker=4, n=size),
                replica('ssd', worker=4, n=size, tier='ssd', storage_node='node2')]
    relaxed = restore_plan(replicas, [demand], ws, profile, 'resource_lp')
    assert any(op['replica_id'] == 'ssd' for op in relaxed['ops'])
    runtime = Cluster([], profile)
    actual = restore_plan(replicas, [demand], ws, runtime.profile, 'resource_lp')
    validate_plan(actual)
    assert {op['replica_id'] for op in actual['ops']} == {'dram'}
    assert 'serial_target_execution' not in profile
