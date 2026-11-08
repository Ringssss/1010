"""Primary metric comparison; account separately for planning moved before load_begin."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT/'codex_coding/src/task2/cluster'))
from analyze_restore_profile import loading


def main(directory):
    data = json.loads((directory/'summary.json').read_text())
    assert data['status'] == 'passed' and not data['cleanup_errors'] and not data['source_hash_mismatches']
    assert len(data['restores']) == 2
    rows = []
    for r in data['restores']:
        assert r['passed'] and r['verification']['passed'] and r['verification']['checked_bytes'] == 70329718112
        assert r['chunk_bytes'] == 32 << 20
        assert all(w['staging_bytes'] == 64 << 20 for w in r['flow']['execution'])
        window = loading(r)
        assert abs(sum(window['segments_s'].values())-window['load_s']) < 1e-6
        rows.append({'variant': r['variant'], 'load_s': window['load_s'], 'segments_s': window['segments_s'],
            'execute_s': r['flow']['execute_wall_s'], 'preplanning_s': r['preplanning_s'],
            'dispatch_to_ready_s': r['dispatch_to_ready_s'], 'byte_plan_sha256': r['byte_plan_sha256'],
            'verified_bytes': r['verification']['checked_bytes'], 'generation_checks': len(r['replies'])})
    a, b = rows
    assert a['byte_plan_sha256'] == b['byte_plan_sha256']
    assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest() == sha for p, sha in data['code_sha256'].items())
    result = {'development': 'v0.1.23.3', 'task': 'task2', 'passed': True, 'cases': rows,
        'primary_metric': 'same-host max(weights_ready)-min(load_begin)',
        'load_saved_s': a['load_s']-b['load_s'], 'load_reduction_pct': 100*(1-b['load_s']/a['load_s']),
        'preplanning_cost_s': b['preplanning_s'], 'saved_minus_preplanning_s': a['load_s']-b['load_s']-b['preplanning_s'],
        'segment_saved_s': {k: a['segments_s'][k]-b['segments_s'][k] for k in a['segments_s']},
        'limitations': ['one observation per variant; fixed baseline then preplanned order',
                       'same source generations and byte routes; both communication-warm',
                       'one-shot ticket consumes the same transactional prepare checks; not a stale-plan bypass',
                       'advance planning is possible only when the target and placement are known',
                       'negative saved time is a regression; prediction accuracy is not a success criterion']}
    (directory/'load_gain.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--run-dir', type=Path, required=True)
    main(parser.parse_args().run_dir)
