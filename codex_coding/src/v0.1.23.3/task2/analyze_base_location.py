"""Audit primary loading gain, equal steady memory, and separately charged setup peaks."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT/'codex_coding/src/task2/cluster'))
from analyze_restore_profile import loading

GIB = 1 << 30


def main(directory):
    data = json.loads((directory/'summary.json').read_text())
    assert data['status'] == 'passed' and not data['cleanup_errors'] and not data['source_hash_mismatches']
    assert len(data['restores']) == 2
    assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest() == sha for p, sha in data['code_sha256'].items())
    cases = []
    for row in data['restores']:
        assert row['passed'] and row['verification']['passed']
        assert row['verification']['checked_bytes'] == 70329718112
        assert len(row['replies']) == 3
        assert row['flow']['executor'] == 'serial' and row['flow']['plan'].get('chunk_bytes', 32 << 20) == 32 << 20
        assert all(w['staging_bytes'] == 64 << 20 for w in row['flow']['execution'])
        window = loading(row)
        cases.append({'variant': row['variant'], 'load_s': window['load_s'], 'segments_s': window['segments_s'],
            'execute_s': row['flow']['execute_wall_s'], 'preplanning_s': row['preplanning_s'],
            'path_GiB': {k: n/GIB for k, n in row['path_bytes'].items()},
            'steady_bytes': row['steady_bytes'], 'verified_bytes': row['verification']['checked_bytes'], 'generation_checks': len(row['replies'])})
    a, b = cases
    assert a['variant'] == 'remote_base' and b['variant'] == 'local_base'
    assert a['steady_bytes'] == b['steady_bytes']
    assert a['path_GiB']['local_hbm'] == b['path_GiB']['local_hbm'] == 8
    assert b['path_GiB'].get('network', 0) == 0
    # Verify the fixed replicas retained their owners, extents and generations.
    layouts = data['layouts']
    def cache_rows(layout):
        return [r for r in layout['replicas'] if r['id'] in data['fixed_cache_ids']]
    # LayoutRun currently exports rows under 'replicas'; fail if the schema differs.
    assert cache_rows(layouts['remote_base']) == cache_rows(layouts['local_base'])
    setup = {}
    for prefix in ('base/remote', 'base/local'):
        rows = [r for r in data['base_preparation'] if r['prefix'] == prefix]
        assert len(rows) == 2
        setup[prefix] = {'wall_s': sum(r['wall_s'] for r in rows),
                         'execute_s': sum(r['flow']['execute_wall_s'] for r in rows),
                         'copied_bytes': sum(r['flow']['verification']['checked_bytes'] for r in rows),
                         'peak_catalog_bytes': {t: max(r['peak_catalog_bytes'][t] for r in rows) for t in ('dram', 'hbm')}}
    archives = json.loads((ROOT/'codex_coding/results/v0.1.23.3/task2/archive_baseline_0930.json').read_text())
    changed = [r['path'] for r in archives if hashlib.sha256((ROOT/r['path']).read_bytes()).hexdigest() != r['sha256']]
    assert not changed
    saved = a['load_s']-b['load_s']
    result = {'development': 'v0.1.23.3', 'task': 'task2', 'passed': True, 'cases': cases,
        'validation_topology': data.get('validation_topology'),
        'primary_metric': 'same-host max(weights_ready)-min(load_begin)',
        'load_saved_s': saved, 'load_reduction_pct': 100*saved/a['load_s'], 'speedup': a['load_s']/b['load_s'],
        'execute_saved_s': a['execute_s']-b['execute_s'],
        'segment_saved_s': {k: a['segments_s'][k]-b['segments_s'][k] for k in a['segments_s']},
        'base_preparation': setup, 'cache_build_wall_s': data['cache_build_wall_s'],
        'migration_amortization_restores': setup['base/local']['wall_s']/saved if saved > 0 else None,
        'migration_amortization_scope': 'if the full verified experimental relocation is paid at runtime; not a deployment cost estimate',
        'archive_files_changed': changed,
        'limitations': ['one restore per layout, fixed remote-then-local order; not a statistical guarantee',
          'read validation_topology: occupied original GPUs may require a different target and explicitly matched non-nearest NICs',
          'known target and nearest-NUMA capacity required; other target nodes can become slower',
          'same steady global budgets and fixed replicas; node-level DRAM distribution changes',
          'full-rank preparation temporarily duplicates a base; no bounded online migration claim',
          'catalog peak excludes model skeleton and allocator reserve; these are not device-wide high-water marks',
          'this validates manually specified initial-base locations, not automatic base-placement optimization']}
    (directory/'base_location_gain.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--run-dir', required=True, type=Path)
    main(p.parse_args().run_dir)
