"""Validate fitting, communication incarnation and non-double-counted dependencies."""
import copy
import pytest
from fluidckpt.cluster.service_cost import fit_path, new_state, estimate_execution, observe_success
from fluidckpt.cluster.path_catalog import topology_id
from fluidckpt.cluster.service_cost import fit_request_model


def test_affine_fit():
    c = fit_path({'nbytes': 10**8, 'service_s': .42},
                 {'nbytes': 10**8, 'service_s': .02}, {'nbytes': 10**9, 'service_s': .11})
    assert c['operation_s'] == pytest.approx(.01)
    assert c['warm_GBps'] == pytest.approx(10)
    assert c['first_pair_s'] == pytest.approx(.4)


def test_waits_pair_activation_and_epochs():
    ws = [dict(worker=i, node='n', gpu=i, numa=0, session='s', epoch=str(i)) for i in range(3)]
    model = {'schema_version': 1, 'executor': 'serial', 'topology_id': topology_id(ws), 'dispatch_s': .01,
             'scope': 'test', 'paths': {k: {'warm_GBps': 1, 'operation_s': 0, 'first_pair_s': .5} for k in ('a', 'b')}}
    plan = {'estimated_transfer_s': .3, 'ops': [dict(op_id=i, src_w=s, dst_w=2, path=p, n=10**8)
            for i, (s, p) in enumerate([(0, 'a'), (1, 'b'), (0, 'a')])]}
    state = new_state(ws)
    result = estimate_execution(plan, ws, model, state)
    assert result['estimated_execute_s'] == pytest.approx(1.31)
    assert result['timeline'][1]['source_wait_s'] == pytest.approx(.6)
    assert state['warm_pairs'] == []
    observe_success(plan, ws, state)
    assert estimate_execution(plan, ws, model, state)['estimated_execute_s'] == pytest.approx(.31)
    ws[0]['epoch'] = 'new'
    with pytest.raises(ValueError, match='incarnations'):
        estimate_execution(plan, ws, model, state)


def test_equal_byte_batch_separates_request_and_operation():
    ws = [dict(worker=0, node='n', gpu=0, numa=0)]
    rows = []
    for label, n, count, cold in [('warm_small', 128e6, 1, 0), ('warm_batch', 128e6, 4, 0),
                                 ('warm_large', 256e6, 1, 0), ('cold_small', 128e6, 1, .4)]:
        rows.append(dict(label=label, path='p', nbytes=n, operations=count,
                         target_s=.002+.0001*count+n/20e9+cold, dispatch_s=.003))
    model = fit_request_model(ws, rows)
    assert model['request_s'] == pytest.approx(.002)
    assert model['paths']['p']['operation_s'] == pytest.approx(.0001)
    assert model['paths']['p']['warm_GBps'] == pytest.approx(20)


def test_service_layout_rejects_extra_cold_pair_and_can_apply():
    from test_managed import make_case, MemoryCluster, PROFILE, G
    from fluidckpt.cluster.managed import solve_joint_request
    from fluidckpt.cluster.planning import path_for
    ws, q = make_case(n=G)
    # One quarter-rank of cache fits; using it saves bandwidth but activates another pair.
    q['budget']['tier_bytes']['hbm'] = G//2
    q['block_bytes'] = G//4
    q['budget']['locations'] = [{'worker': 1, 'tier': 'hbm', 'max_bytes': G//2}]
    q['targets'] = q['targets'][:1]; q['base_ids'] = q['base_ids'][:1]
    profile = copy.deepcopy(PROFILE)
    model = {'schema_version': 2, 'executor': 'serial', 'topology_id': topology_id(ws), 'request_s': .002,
             'dispatch_s': .003, 'scope': 'synthetic test', 'paths': {}}
    for src, tier in ((0, 'dram'), (1, 'hbm')):
        path = path_for(dict(worker=src, tier=tier, numa=ws[src]['numa']), q['targets'][0],
                        {w['worker']: w for w in ws}, profile)
        model['paths'][path['path']] = dict(warm_GBps=10 if tier == 'dram' else 100, operation_s=0, first_pair_s=.4)
        profile.setdefault('directed_path_rates', {})[path['path']] = model['paths'][path['path']]['warm_GBps']
    q.update(service_model=model, service_pair_policy='cold')
    plan = solve_joint_request(ws, q, profile)
    assert plan['solver']['status'] == 'service_reranked'
    proposals = {r['name']: r['predicted_s'] for r in plan['solver']['proposals']}
    assert proposals['bandwidth_milp'] > proposals['base_only']
    assert plan['selected'] == [] and plan['decision'] == 'keep_current'
    cluster = MemoryCluster(ws); cluster.profile = profile
    cluster.apply_joint(plan)
    restored = cluster.plan_managed_restore(plan, q['targets'])
    assert restored['service_prediction']['estimated_execute_s'] == pytest.approx(plan['predicted_after_s'])
