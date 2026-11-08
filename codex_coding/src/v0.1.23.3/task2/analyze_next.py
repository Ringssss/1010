"""Audit the four-stage acceptance without modifying archival documents."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[4]


def main(args):
    full = json.loads((args.full/'analysis.json').read_text())
    discovery = json.loads((args.discovery/'summary.json').read_text())
    multi = json.loads((args.multi/'summary.json').read_text())
    assert full['passed']
    for run in (discovery, multi):
        assert run['status'] == 'passed' and not run['cleanup_errors'] and not run['source_hash_mismatches']
    changed = [name for name, sha in multi['code_sha256'].items() if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != sha]
    assert not changed, changed
    archive = json.loads((ROOT/'codex_coding/results/v0.1.23.3/task2/archive_baseline_0930.json').read_text())
    archive_changed = [r['path'] for r in archive if hashlib.sha256((ROOT/r['path']).read_bytes()).hexdigest() != r['sha256']]
    assert not archive_changed, archive_changed
    tests = args.tests.read_text()
    match = re.search(r'(\d+) passed', tests)
    assert match and ' failed' not in tests
    m = multi['multi_acceptance']; p = m['initial_plan']
    assert m['low_reuse_plan']['decision'] == 'keep_current'
    assert p['decision'] == 'replace' and p['economics']['allowed']
    assert m['old_profile_rejected'] and m['feedback_restore']['verification']['passed']
    restores = []
    for r in m['restores']:
        f = r['flow']; assert f['verification']['passed'] and f['plan']['strategy'] == 'resource_lp'
        restores.append({'scenario': r['scenario'], 'verified_bytes': f['verification']['checked_bytes'],
            'predicted_transfer_s': f['plan']['estimated_transfer_s'], 'execute_s': f['execute_wall_s']})
    assert len(restores) == 2
    result = {'development': 'v0.1.23.3', 'task': 'task2', 'passed': True, 'cpu_tests_passed': int(match.group(1)),
        'step1_full_model': full['models'], 'step2_paths': discovery['discovery_audit'],
        'step3_objective': p['request']['objective'], 'step3_scenario_costs': p['scenario_costs'],
        'step3_restores': restores, 'step3_final_usage': m['applied']['final_usage'],
        'step4_economics': p['economics'], 'step4_old_profile_rejected': m['old_profile_rejected'],
        'step4_feedback': m['feedback'], 'step4_replanned_decision': m['replanned']['decision'],
        'step4_verified_bytes': m['feedback_restore']['verification']['checked_bytes'],
        'archive_files_changed': archive_changed, 'current_source_matches_multi_run': True,
        'limitations': ['full model validated in step1; later multi-target changes validated with synthetic complete snapshots',
          'mutually exclusive future targets, not concurrent multi-target recovery',
          'one-sample isolated bandwidth; concurrent link capacities still provisional',
          'migration gate uses explicit cost assumptions; online demand prediction is not implemented',
          'migration benefit gate evaluates the restore-optimal proposal; it does not globally optimize amortized migration cost',
          'immutable complete base and single coordinator; no process-loss HA or global atomic rollback',
          'feedback is explicitly ingested; expiry requires rediscovery rather than silent stale-path reuse']}
    (args.multi/'delivery.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('full', 'discovery', 'multi', 'tests'):
        parser.add_argument('--'+name, type=Path, required=True)
    main(parser.parse_args())
