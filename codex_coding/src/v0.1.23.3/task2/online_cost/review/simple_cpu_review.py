"""Independent CPU review of the single sender DAG and coexistence gates."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
EXECUTION = HERE.parent / 'execution'
sys.path[:0] = [str(EXECUTION), str(HERE.parent / 'model')]
from cpu_check import fixtures
from simple_contracts import GiB, RANK_BYTES, layout_plan, serialized_sender_evidence
from simple_resources import resource_snapshot
from predictor import plan_workload
from fluidckpt.cluster.path_catalog import topology_id
from fluidckpt.cluster.service_cost import estimate_execution, new_state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    checks = []
    def check(name, condition):
        checks.append(dict(name=name, passed=bool(condition)))
    workers, manifests, replicas = fixtures()
    workers = workers[:3]
    for row in replicas:
        if row['worker'] == 3:
            row['worker'], row['epoch'] = 2, workers[2]['epoch']
    plans = {name: layout_plan(manifests, replicas, workers, ['CPU'] * 3, name, name, 'CPU')
             for name in ('C0_mixed', 'Hnear', 'Hremote')}
    check('same_complete_66op_70329718112byte_2096chunk_work', all(
        len(p['ops']) == 66 and plan_workload(p)['N'] == 70329718112 and plan_workload(p)['C'] == 2096
        for p in plans.values()) and len({json.dumps(plan_workload(p), sort_keys=True) for p in plans.values()}) == 1)
    check('C0_exact_prefix_remote16GiB_per_rank_then_local',
          [o['kind'] for o in plans['C0_mixed']['ops']] == ['local_hbm'] * 2 + ['network'] * 32 + ['local_dram'] * 32)
    check('remote_rank0_NUMA4_rank1_NUMA7_on_same_sender', all(
        o['src_w'] == 2 and o['path'] == f"2:dram:{4 if o['tp'] == 0 else 7}->{o['dst_w']}"
        for o in plans['Hremote']['ops'] if o['kind'] == 'network'))
    paths = {o['path']: dict(warm_GBps=1., operation_s=0., first_pair_s=0.)
             for p in plans.values() for o in p['ops']}
    model = dict(schema_version=2, topology_id=topology_id(workers), executor='serial',
                 chunk_bytes=32 << 20, paths=paths, dispatch_s=0., request_s=0., scope='CPU review fixture')
    state = new_state(workers)
    state['warm_pairs'] = ['0:2', '1:2']
    service = {name: estimate_execution(p, workers, model, state) for name, p in plans.items()}
    check('native_shared_sender_DAG_sums_remote_two_ranks',
          abs(service['Hremote']['estimated_worker_s'] - (GiB + 2 * (RANK_BYTES - GiB)) / 1e9) < 1e-9)
    check('native_local_rank_streams_remain_parallel',
          abs(service['Hnear']['estimated_worker_s'] - RANK_BYTES / 1e9) < 1e-9)
    remote_ids = [o['op_id'] for o in plans['Hremote']['ops'] if o['kind'] == 'network']
    events = [dict(ev=ev, op_id=oid, monotonic_ns=100 + index * 10 + offset, clock_domain='CPU_sender')
              for index, oid in enumerate(remote_ids)
              for ev, offset in (('cluster_op_begin', 0), ('cluster_op_end', 5))]
    check('ordered_source_evidence_covers64_remote_ops', serialized_sender_evidence(
        [dict(worker=2, op_clock_events=events)], plans['Hremote'])['passed'])
    broken = copy.deepcopy(events)
    broken[2]['monotonic_ns'] = broken[1]['monotonic_ns'] - 1
    try:
        serialized_sender_evidence([dict(worker=2, op_clock_events=broken)], plans['Hremote'])
        rejected = False
    except ValueError:
        rejected = True
    check('source_overlap_rejected_without_cross_host_clock_math', rejected)
    roles = [dict(w, boot_id='CPU_boot', bdf=f'CPU_bdf{i}') for i, w in enumerate(workers)]
    budgets = {node: dict(task_dram_cap_bytes=(192 if node == 'node27' else 96) * GiB,
                         container_hard_cap_bytes=(224 if node == 'node27' else 128) * GiB,
                         ongoing_host_available_floor_bytes=(64 if node == 'node27' else 32) * GiB,
                         per_selected_gpu_hbm_cap_bytes=48 * GiB, task_hbm_cap_bytes=(96 if node == 'node27' else 2) * GiB,
                         ongoing_gpu_free_floor_bytes=2 * GiB) for node in ('node27', 'node30')}
    observed = []
    for i, worker in enumerate(workers):
        observed.append(dict(worker, hbm_total_bytes=64 * GiB,
            headroom_bytes=dict(hbm=(15 if i == 2 else 60) * GiB, dram=55 * GiB),
            numa_headroom_bytes={'4': 55 * GiB, '7': 52 * GiB},
            hbm_allocated_bytes=64 << 20, hbm_reserved_bytes=128 << 20,
            own_hbm_ledger=dict(known_unique_tensor_bytes=64 << 20),
            used_bytes=dict(dram=0, hbm=0, ssd=0),
            runtime_identity=dict(boot_id='CPU_boot', actual_bdf=f'CPU_bdf{i}', pci_numa_node=worker['numa'],
                cgroup_memory_current_bytes=2 * GiB, cgroup_memory_max_bytes=budgets[worker['node']]['container_hard_cap_bytes'],
                host_memory_available_bytes=300 * GiB)))
    def gate(rows, split=None):
        return resource_snapshot(rows, roles, budgets, 'CPU_clock', allocation_by_worker={2: 2 * RANK_BYTES},
                                 allocation_by_numa={2: split or {4: RANK_BYTES, 7: RANK_BYTES}})
    result = gate(observed)
    check('foreign49GiB_whole_card_used_not_charged_to_own2GiB', result['passed'] and
          result['nodes']['node30']['cards'][0]['whole_card_used_bytes'] == 49 * GiB)
    check('actual_total_driver_RCCL_unknown_is_explicit', result['task_hbm_actual_bytes'] is None and
          result['task_hbm_attribution_complete'] is False and result['peak_verified'] is False)
    for label, field, value in [('low_whole_card_headroom', 'headroom_bytes', dict(hbm=GiB, dram=55 * GiB)),
                                ('torch_reserved_over2GiB', 'hbm_reserved_bytes', 2 * GiB + 1),
                                ('known_unique_allocations_over2GiB', 'own_hbm_ledger', dict(known_unique_tensor_bytes=2 * GiB + 1))]:
        bad = copy.deepcopy(observed)
        bad[2][field] = value
        check(label + '_stops', not gate(bad)['passed'])
    check('both_rank_copies_cannot_be_admitted_to_one_NUMA', not gate(observed, {4: 2 * RANK_BYTES})['passed'])
    bad = copy.deepcopy(observed)
    bad[2]['runtime_identity']['pci_numa_node'] = 7
    check('GPU_PCI_NUMA_change_is_not_hidden_by_source_NUMA7', not gate(bad)['passed'])
    output = dict(passed=all(c['passed'] for c in checks), checks=checks,
                  scope='CPU review only; no GPU, Torch, SSH, Docker or model execution.',
                  actual_model_online_qualified=False, concurrent_multiNIC_qualified=False,
                  source_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in EXECUTION.glob('simple_*.py')})
    (args.out / 'qualification.json').write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps(dict(passed=output['passed'], checks=len(checks), out=str(args.out))))
    raise SystemExit(0 if output['passed'] else 1)


if __name__ == '__main__':
    main()
