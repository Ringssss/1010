"""Report the primary weight-loading metric and additive attribution, not proxy speedup."""
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
    cases = []
    for row in data['restores']:
        assert row['passed'] and row['verification']['passed']
        assert row['verification']['checked_bytes'] == 70329718112
        assert all(w['staging_bytes'] == 64 << 20 for w in row['flow']['execution'])
        window = loading(row)
        assert abs(sum(window['segments_s'].values())-window['load_s']) < 1e-6
        cases.append({'chunk_bytes': row['chunk_bytes'], 'load_s': window['load_s'],
                      'execute_s': row['flow']['execute_wall_s'], 'segments_s': window['segments_s'],
                      'byte_plan_sha256': row['byte_plan_sha256'], 'verified_bytes': row['verification']['checked_bytes'],
                      'generation_checks': len(row['replies'])})
    a, b = cases
    assert a['byte_plan_sha256'] == b['byte_plan_sha256']
    result = {'development': 'v0.1.23.3', 'task': 'task2', 'passed': True, 'cases': cases,
        'primary_metric': 'same-host max(weights_ready)-min(load_begin)',
        'load_saved_s': a['load_s']-b['load_s'], 'load_reduction_pct': 100*(1-b['load_s']/a['load_s']),
        'execute_saved_s': a['execute_s']-b['execute_s'],
        'segment_saved_s': {k: a['segments_s'][k]-b['segments_s'][k] for k in a['segments_s']},
        'limitations': ['one observation per variant, fixed order 32 then 64; not a statistical speedup',
                       'same published placement and byte routes, same memory budget, both communication-warm',
                       'no prediction-model improvement claim; direct executor change only'],
        'source_matches': all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest() == sha for p, sha in data['code_sha256'].items())}
    assert result['source_matches']
    (directory/'load_gain.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--run-dir', type=Path, required=True)
    main(parser.parse_args().run_dir)
