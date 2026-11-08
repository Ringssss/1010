"""Compact transport must preserve byte plans, manifests and stale-plan rejection."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fluidckpt.cluster.catalog import Catalog
from fluidckpt.cluster.control import Cluster
from fluidckpt.cluster.planning import restore_plan, validate_plan
from fluidckpt.cluster.wire import planning_payload, bind_manifests, worker_plan, planning_context, checked_epochs
from test_cluster import PROFILE, workers, replica, target


def full_inputs():
    ws = workers()
    for w in ws:
        w.update(epoch=f"session/worker{w['worker']}/nonce", session="session", replicas=[])
    rs = [replica(epoch=ws[0]["epoch"]), replica("partial", 2, 17, 40_000_003, epoch=ws[2]["epoch"])]
    for r in rs:
        r.update(meta={"tensors": [{"name": f"weight{i}", "shape": [32, 32]} for i in range(40)]},
                 index={f"weight{i}": [i*4096, 4096] for i in range(40)})
        ws[r["worker"]]["replicas"].append(r)
    ts = [dict(target(), mode="replica", id="target", tier="hbm", meta=rs[0]["meta"], index=rs[0]["index"])]
    return rs, ts, ws


@pytest.mark.parametrize("strategy", ["nearest", "fastest", "balanced", "resource_lp"])
def test_plans_and_manifests_identical(strategy):
    rs, ts, ws = full_inputs()
    full = restore_plan(rs, ts, ws, PROFILE, strategy)
    wire = planning_payload(rs, ts, ws, PROFILE, strategy)
    lean = restore_plan(wire["replicas"], wire["targets"], wire["workers"], wire["profile"], strategy)
    bound = bind_manifests(json.loads(json.dumps(lean)), ts)
    assert bound["ops"] == full["ops"]
    assert bound["targets"] == full["targets"]
    assert len(json.dumps(wire)) < len(json.dumps(dict(replicas=rs, targets=ts, workers=ws)))/2
    for worker in range(len(ws)):
        body = worker_plan(bound, worker)
        validate_plan(body)
        assert body["ops"] == bound["ops"]
        assert ("meta" in body["targets"][0]) == (worker == ts[0]["worker"])


@pytest.mark.parametrize("field,value", [("worker", 5), ("n", 10), ("identity", "different"), ("tp", 7)])
def test_bad_planner_target_not_hidden_by_rehydration(field, value):
    rs, ts, ws = full_inputs()
    payload = planning_payload(rs, ts, ws, PROFILE, "nearest")
    p = restore_plan(payload["replicas"], payload["targets"], payload["workers"], PROFILE)
    p["targets"][0][field] = value
    with pytest.raises(ValueError): bind_manifests(p, ts)


def test_compact_inventory_does_not_copy_tensor_layout():
    class Expensive:
        def __deepcopy__(self, memo): raise AssertionError("layout should not be copied")
    catalog = Catalog({"dram": 8192})
    catalog.reserve(replica(n=32, meta=Expensive(), index=Expensive()), lambda: (object(), None))
    catalog.publish("r", True)
    row = catalog.inventory(compact=True)[0]
    assert row["state"] == "VALID" and row["generation"] == 1
    assert not {"meta", "index", "owner", "tensor"}.intersection(row)


def test_numa_binding_survives_planning_and_allocation():
    from fluidckpt.cluster.worker import ClusterWorker
    from fluidckpt.serving.replicas import identity
    meta = {'model_identity': 'binding-test', 'tp_size': 1, 'tp_rank': 0, 'tensors': []}
    index = {'bytes': [0, 4096]}
    model = identity(meta, index)
    target = {'key': 'target', 'id': 'target', 'worker': 0, 'identity': model, 'tp': 0,
              'off': 0, 'n': 4096, 'mode': 'replica', 'tier': 'dram', 'numa': 1,
              'meta': meta, 'index': index}
    assert planning_payload([], [target], [], PROFILE, 'nearest')['targets'][0]['numa'] == 1
    op = {'op_id': 0, 'target': 'target', 'src_w': 1, 'dst_w': 0, 'identity': model,
          'tp': 0, 'off': 0, 'n': 4096, 'source_offset': 0}
    worker = SimpleNamespace(rank=0, epoch='e', runs={}, catalog=Catalog({'dram': 8192}),
        a=SimpleNamespace(numa=0), node='node0', gpu=0, record_targets=lambda *args: None,
        allocate=lambda desc: (object(), None))
    result = ClusterWorker.prepare(worker, {'tx': 'numa', 'epochs': {'0': 'e'},
        'plan': {'targets': [target], 'ops': [op]}})
    assert result['prepared']
    assert worker.catalog.entries['target']['numa'] == 1


def test_snapshot_cannot_be_used_with_different_endpoints():
    _, _, ws = full_inputs()
    endpoints = [f"http://worker{i}" for i in range(len(ws))]
    context = planning_context(ws, endpoints)
    assert checked_epochs(context, endpoints) == {str(w["worker"]): w["epoch"] for w in ws}
    with pytest.raises(ValueError): checked_epochs(context, list(reversed(endpoints)))
    context["epochs"].pop("0")
    with pytest.raises(ValueError): checked_epochs(context, endpoints)


def test_compact_rpc_returns_full_execution_manifests():
    rs, ts, ws = full_inputs()
    cluster = Cluster([str(i) for i in range(len(ws))], PROFILE, "planner")
    def remote(endpoint, operation, body, telemetry):
        assert operation == "plan_restore"
        assert all("meta" not in r for r in body["replicas"])
        return restore_plan(body["replicas"], body["targets"], body["workers"], body["profile"], body["strategy"])
    with patch("fluidckpt.cluster.control.request", side_effect=remote):
        result = cluster.solve_restore(rs, ts, ws)
    assert result["targets"][0]["meta"] == ts[0]["meta"]
    assert result["control_context"]["epochs"]["0"] == ws[0]["epoch"]


def test_transfer_reuses_snapshot_instead_of_querying_again():
    rs, ts, ws = full_inputs()
    cluster = Cluster([str(i) for i in range(len(ws))], PROFILE)
    plan = cluster.solve_restore(rs, ts, ws)
    def rpc(operation, bodies=None, telemetry=None):
        if operation == "execute":
            return [{"records": [{"op_id": o["op_id"], "bytes": o["n"], "role": "target"}
                                  for o in plan["ops"] if o["dst_w"] == i]} for i in range(len(ws))]
        return [{} for _ in ws]
    with patch.object(cluster, "inventory", side_effect=AssertionError("redundant query")), patch.object(cluster, "call_all", side_effect=rpc):
        result = cluster.transfer(plan, verify_before_ready=False)
    assert result["epoch_snapshot_reused"] and not result["rpc"]["inventory"]
    assert not cluster.active


@pytest.mark.parametrize("change", ["worker_restart", "replica_replaced"])
def test_stale_snapshot_is_rejected_at_prepare(change):
    from fluidckpt.cluster.worker import ClusterWorker
    rs, ts, ws = full_inputs()
    catalog = Catalog({"dram": 1 << 30})
    catalog.reserve(rs[0], lambda: (object(), None)); catalog.publish("r", True)
    plan = restore_plan([rs[0]], ts, ws, PROFILE)
    epoch = ws[0]["epoch"]
    if change == "worker_restart": epoch += "/new-incarnation-with-same-pid"
    else:
        catalog.retire("r"); catalog.reserve(rs[0], lambda: (object(), None)); catalog.publish("r", True)
    worker = SimpleNamespace(rank=0, epoch=epoch, runs={}, catalog=catalog)
    with pytest.raises(ValueError):
        ClusterWorker.prepare(worker, {"tx": "test", "epochs": {"0": ws[0]["epoch"]}, "plan": worker_plan(plan, 0)})
    assert not catalog.transactions and not worker.runs
