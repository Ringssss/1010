"""Check exact native plan contracts and capture-marker failure behavior without GPU imports."""
import argparse
import ast
import gc
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / 'model')]
from contracts import GiB, CHUNK, layout_plan, paired_overlap, resource_snapshot, physical_park_snapshot
from predictor import plan_workload


def fixtures():
    workers = [{'worker': i, 'node': 'node27' if i < 2 else 'node30', 'gpu': 4 + i % 2,
                'numa': 4 if i % 2 == 0 else 7, 'session': 'CPU_fixture', 'epoch': f'CPU_epoch{i}',
                'capabilities': ['local_dram', 'local_hbm', 'network', 'peer', 'local_ssd']} for i in range(4)]
    manifests = [{'identity': f'CPU_rank{r}', 'tp': r, 'n': 35164859056,
                  'meta': {'model_identity': 'CPU_fixture_no_weights', 'tp_size': '2', 'tp_rank': r},
                  'index': {'CPU_storage': [0, 35164859056]}} for r in (0, 1)]
    replicas = []
    for rank, m in enumerate(manifests):
        for offset in range(0, m['n'], GiB):
            replicas.append({'id': f'CPU/base/{rank}/{offset}', 'identity': m['identity'], 'tp': rank,
                             'off': offset, 'n': min(GiB, m['n'] - offset), 'worker': rank,
                             'tier': 'dram', 'numa': workers[rank]['numa'], 'epoch': workers[rank]['epoch'],
                             'state': 'VALID', 'generation': 1, 'leases': 0})
        for owner, tier, size in ((rank, 'hbm', GiB), (rank + 2, 'dram', m['n'])):
            replicas.append({'id': f'CPU/{tier}/{owner}/{rank}', 'identity': m['identity'], 'tp': rank,
                             'off': 0, 'n': size, 'worker': owner, 'tier': tier, 'numa': workers[owner]['numa'],
                             'epoch': workers[owner]['epoch'], 'state': 'VALID', 'generation': 1, 'leases': 0})
    return workers, manifests, replicas


def capture_test(out, mismatch=False, verify=True):
    import os
    import pickle
    import time
    from fluidckpt.serving.replicas import identity
    tree = ast.parse((HERE / 'worker.py').read_text())
    wrapper = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'verified_capture')
    native_tree = ast.parse((HERE.parents[5] / 'main_lib/fluidckpt/cluster/capture.py').read_text())
    capture = next(n for n in native_tree.body if isinstance(n, ast.FunctionDef) and n.name == 'capture_regions')
    capture.body = [n for n in capture.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
    directory = out / ('capture_failure' if mismatch else 'capture_success' if verify else 'capture_default')
    directory.mkdir()
    pkl, marker = directory / 'rank0.pkl', directory / 'rank0.captured'
    data = b'CPU capture bytes!'
    meta = {'tp_rank': 0, 'model_identity': 'CPU_mock', 'tp_size': '2', 'tensors': []}
    with pkl.open('wb') as stream:
        pickle.dump({'meta': meta, 'entries': [{'name': 'CPU_storage', 'nbytes': len(data)}]}, stream)
    counters, imported, retired, calls = {'token': 1}, [], [], []
    entries = {'CPU/protected': {'bytes': b'protected'}}
    class Tensor:
        def __init__(self, n):
            self.bytes = bytes(n)
    class Target:
        def __init__(self, *args):
            imported.append('single_native_import')
        def __del__(self):
            counters['token'] -= 1
        def gather(self, off, tensor):
            tensor.bytes = data[off:off + len(tensor.bytes)]
            return len(tensor.bytes)
    class Catalog:
        def __init__(self):
            self.entries = entries
        def reserve(self, desc, factory):
            tensor, owner = factory()
            entry = dict(desc, tensor=tensor, owner=owner, state='PREPARING')
            self.entries[desc['id']] = entry
            return entry
        def publish(self, rid, verified):
            self.entries[rid]['state'] = 'VALID'
        def retire(self, rid):
            retired.append(rid)
            self.entries.pop(rid)
    class Worker:
        gpu, dev, rank, node = 0, 'CPU_mock', 0, 'CPU_node'
        a = SimpleNamespace(numa=0)
        epoch = 'CPU_epoch'
        catalog = Catalog()
        def _my_pkls(self, tag, phase):
            return [(pkl, meta)]
        def allocate(self, desc):
            return Tensor(desc['n']), None
        def extent_digest(self, entry, off, n):
            if marker.exists():
                raise ValueError('captured marker appeared before full SHA')
            return hashlib.sha256(entry['tensor'].bytes).hexdigest()
        def target_digest(self, target, off, n):
            return hashlib.sha256(b'mismatch' if mismatch else data[off:off + n]).hexdigest()
        def log(self, **fields):
            calls.append(fields['ev'])
    cuda = SimpleNamespace(set_device=lambda *args: None, synchronize=lambda *args: calls.append('sync'), ipc_collect=lambda: calls.append('ipc_collect'))
    namespace = {'HbmTarget': Target, 'gc': gc, 'Path': Path, 'json': json, 'os': os, 'pickle': pickle,
                 'time': time, 'identity': identity, 'torch': SimpleNamespace(cuda=cuda),
                 'native': SimpleNamespace(torch=SimpleNamespace(cuda=cuda))}
    exec(compile(ast.Module(body=[capture], type_ignores=[]), '<CPU native capture contract>', 'exec'), namespace)
    namespace['original_capture'] = namespace['capture_regions']
    exec(compile(ast.Module(body=[wrapper], type_ignores=[]), '<CPU wrapper capture contract>', 'exec'), namespace)
    failed = False
    try:
        if verify:
            result = namespace['verified_capture'](Worker(), tag='CPU', snap='CPU', block_bytes=4096)
        else:
            result = namespace['capture_regions'](Worker(), tag='CPU', snap='CPU', block_bytes=4096)
    except RuntimeError:
        failed, result = True, None
    single_mapping = imported == ['single_native_import'] and counters['token'] == 0
    if mismatch:
        return failed and single_mapping and not marker.exists() and retired == ['CPU/rank0/block0'] and 'CPU/protected' in entries
    if not verify:
        return not failed and single_mapping and marker.exists() and 'verification' not in result[0]
    return not failed and single_mapping and marker.exists() and json.loads(marker.read_text())['full_sha_passed'] is True and result[0]['verification']['checked_bytes'] == len(data) and not retired


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    checks = []
    def check(name, value):
        checks.append({'name': name, 'passed': bool(value)})
    workers, manifests, replicas = fixtures()
    plans = {name: layout_plan(manifests, replicas, workers, ['CPU'] * 4, name, 'CPU_' + name, 'CPU')
             for name in ('C0_mixed', 'Hnear', 'Hremote')}
    for name, plan in plans.items():
        check(name + '_exact_full_coverage_66ops', len(plan['ops']) == 66 and sum(o['n'] for o in plan['ops']) == 70329718112)
        check(name + '_rank_interleaved', [o['dst_w'] for o in plan['ops']] == [0, 1] * 33)
        check(name + '_source_offsets_valid', all(o['source_epoch'] == workers[o['src_w']]['epoch'] and o['n'] <= GiB for o in plan['ops']))
        check(name + '_complete_live_targets', all(t['mode'] == 'live' and t['off'] == 0 and t['n'] == 35164859056 for t in plan['targets']))
    check('fixed_extent_workload_across_layouts', len({json.dumps(plan_workload(p), sort_keys=True) for p in plans.values()}) == 1)
    check('C0_contiguous16remote_extents_per_rank', [o['kind'] for o in plans['C0_mixed']['ops']] == ['local_hbm'] * 2 + ['network'] * 32 + ['local_dram'] * 32)
    check('Hnear_cache_then_local', [o['kind'] for o in plans['Hnear']['ops']] == ['local_hbm'] * 2 + ['local_dram'] * 64)
    check('Hremote_cache_then_remote', [o['kind'] for o in plans['Hremote']['ops']] == ['local_hbm'] * 2 + ['network'] * 64)
    check('full_model_tail_805120688_each', [o['n'] for o in plans['Hremote']['ops'][-2:]] == [805120688] * 2)
    check('native32MiB_total2096chunks', plan_workload(plans['Hnear'])['C'] == 2096)
    try:
        layout_plan(manifests, [r for r in replicas if not (r['worker'] == 2 and r['tier'] == 'dram')], workers, ['CPU'] * 4, 'Hremote', 'CPU_missing', 'CPU')
        rejected = False
    except ValueError:
        rejected = True
    check('missing_assigned_source_no_fallback', rejected)
    plan = plans['Hremote']
    ids = [o['op_id'] for o in plan['ops'] if o['kind'] == 'network'][:2]
    execution = [{'worker': rank, 'op_clock_events': [
        {'ev': 'cluster_op_begin', 'op_id': ids[rank], 'monotonic_ns': 10 + rank * 5, 'clock_domain': 'CPU_target_common'},
        {'ev': 'cluster_op_end', 'op_id': ids[rank], 'monotonic_ns': 30 + rank * 5, 'clock_domain': 'CPU_target_common'}]} for rank in (0, 1)]
    check('actual_overlap_math_common_target_clock', paired_overlap(execution, plan)['overlap_ns'] == 15)
    execution[1]['op_clock_events'][0]['clock_domain'] = 'CPU_other_host'
    execution[1]['op_clock_events'][1]['clock_domain'] = 'CPU_other_host'
    try:
        paired_overlap(execution, plan)
        rejected = False
    except ValueError:
        rejected = True
    check('different_host_clocks_rejected', rejected)
    check('capture_marker_only_after_full_sha', capture_test(a.out))
    check('capture_mismatch_no_marker_precise_retire', capture_test(a.out, mismatch=True))
    check('native_capture_default_no_added_sha_single_mapping', capture_test(a.out, verify=False))
    check('unsigned_duplicate_token_underflow_counterexample', ((1 - 1 - 1) & ((1 << 64) - 1)) > 0)
    roles = [dict(w, bdf=f'CPU_bdf{i}', boot_id='CPU_boot') for i, w in enumerate(workers)]
    budgets = {node: {'task_dram_cap_bytes': (192 if node == 'node27' else 96) * GiB,
                     'container_hard_cap_bytes': (224 if node == 'node27' else 128) * GiB,
                     'per_selected_gpu_hbm_cap_bytes': (48 if node == 'node27' else 1) * GiB,
                     'task_hbm_cap_bytes': (96 if node == 'node27' else 2) * GiB,
                     'ongoing_host_available_floor_bytes': (64 if node == 'node27' else 32) * GiB} for node in ('node27', 'node30')}
    observed = []
    for i, w in enumerate(workers):
        observed.append(dict(w, hbm_total_bytes=64 * GiB, headroom_bytes={'hbm': 64 * GiB - GiB // 2, 'dram': 64 * GiB},
            hbm_allocated_bytes=64 << 20, hbm_reserved_bytes=64 << 20, used_bytes={'dram': 0, 'hbm': 0, 'ssd': 0},
            runtime_identity={'boot_id': 'CPU_boot', 'actual_bdf': f'CPU_bdf{i}', 'pci_numa_node': w['numa'],
                              'cgroup_memory_current_bytes': 2 * GiB, 'cgroup_memory_max_bytes': budgets[w['node']]['container_hard_cap_bytes'],
                              'host_memory_available_bytes': 300 * GiB, 'cgroup_memory_stat': {'anon': GiB, 'file': GiB}}))
    check('resource_snapshot_all_cards_pass', resource_snapshot(observed, roles, budgets, 'CPU_clock')['passed'])
    observed[-1]['headroom_bytes']['hbm'] = 61 * GiB
    facts = resource_snapshot(observed, roles, budgets, 'CPU_clock')
    check('resource_failure_preserves_full_four_worker_facts', not facts['passed'] and len(facts['workers']) == 4 and sum(len(n['cards']) for n in facts['nodes'].values()) == 4)
    check('resource_failure_identifies_concrete_card_values', any(f['reason'] == 'selected_card_hbm_cap' and f['node'] == 'node30' and f['gpu'] == 5 and f['hbm_upper_bytes'] == 3 * GiB and f['hbm_cap_bytes'] == GiB for f in facts['failures']))
    from controller import Run
    run = Run.__new__(Run)
    run.plan, run.roles, run.clock = {'nodes': budgets}, roles, 'CPU_clock'
    run.out = a.out / 'resource_failure_fixture'
    run.out.mkdir()
    try:
        run.gate(observed)
        rejected = False
    except MemoryError as exc:
        rejected = 'node30' in str(exc) and 'hbm_upper_bytes' in str(exc)
    persisted = json.loads((run.out / 'resource_gate_001_failed.json').read_text())
    check('resource_facts_written_before_raise', rejected and len(persisted['workers']) == 4 and run.latest_gate_failure_ref['path'].endswith('resource_gate_001_failed.json'))
    before = [{'headroom_bytes': {'hbm': 0}} for _ in range(2)]
    after = [{'headroom_bytes': {'hbm': 90}} for _ in range(2)]
    events = [{'release_begin': {'allocated_bytes': 100}, 'release_end': {'allocated_bytes': 10}} for _ in range(2)]
    check('physical_pause_exact90percent_both_dimensions', physical_park_snapshot([{'n': 100}] * 2, before, after, events)['passed'])
    after[1]['headroom_bytes']['hbm'] = 89
    check('physical_pause_rejects_insufficient_actual_free_gain', not physical_park_snapshot([{'n': 100}] * 2, before, after, events)['passed'])
    events[0]['release_end']['allocated_bytes'] = 100
    check('release_flag_without_weight_deallocation_rejected', not physical_park_snapshot([{'n': 100}] * 2, before, after, events)['passed'])
    recorded_r02_events = [{'release_begin': {'allocated_bytes': 36496468480},
                            'release_end': {'allocated_bytes': 36496468992}} for _ in range(2)]
    recorded = physical_park_snapshot([{'n': 35164859056}] * 2, before, after, recorded_r02_events)
    check('recorded_r02_minus512_allocated_drop_rejected', not recorded['passed'] and all(r['engine_allocated_drop_bytes'] == -512 for r in recorded['rows']))
    from fluidckpt.cluster.catalog import Catalog
    from fluidckpt.serving.replicas import identity
    from fluidckpt.serving.layout import validate_tensor_layout
    tensors = [{'name': 'CPU_tensor', 'kind': 'param', 'dtype': 'torch.bfloat16', 'shape': [8], 'stride': [1],
                'storage_offset': 0, 'attributes': {}, 'storage_name': 'CPU_tensor'}]
    meta = {'model_identity': 'CPU_identity', 'tp_size': '2', 'tp_rank': 0, 'tensors': tensors}
    check('native_tensor_manifest_dtype_layout_valid', validate_tensor_layout(tensors, tensors) is None and len(identity(meta, {'CPU_tensor': (0, 16)})) == 64)
    for path in HERE.glob('*.py'):
        compile(path.read_text(), str(path), 'exec')
    check('source_syntax', True)
    result = {'scope': 'CPU fixtures only; no Torch import/GPU/SSH/live query or real model payload',
              'actual_model_online_qualified': False, 'passed': all(c['passed'] for c in checks), 'checks': checks,
              'source_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in HERE.glob('*.py')}}
    (a.out / 'qualification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'passed': result['passed'], 'checks': len(checks), 'out': str(a.out)}))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
