"""Reproduce premesh unassigned ranks through the native Cluster interface."""
import argparse
import copy
from pathlib import Path
from common import native, write, fresh_output, require, ref
from runner import Cluster, initialize_mesh, raw_identity_and_empty, raise_cleanup_if_primary_succeeded

def must_fail(action):
    try:
        action()
    except (ValueError, RuntimeError):
        return
    raise AssertionError('expected refusal')

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--actual', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    out = fresh_output(a.out)
    raw = native.read(a.actual / 'startup.inventory.json')
    setup = native.read(a.actual / 'runtime_setup.json')
    checks = []
    for initial_rank in (-1, None):
        rows = copy.deepcopy(raw)
        for row in rows:
            row['worker'] = initial_rank
        calls = []
        cluster = Cluster([w['endpoint'] for w in setup['workers']],{},executor='serial')
        def call_all(operation, bodies=None, telemetry=None):
            calls.append(operation)
            if operation == 'mesh':
                for index,row in enumerate(rows):
                    row['worker'] = index
            return copy.deepcopy(rows)
        cluster.call_all = call_all
        must_fail(cluster.inventory)
        calls.clear()
        case_out = out / ('rank_none' if initial_rank is None else 'rank_minus1')
        case_out.mkdir()
        reports = initialize_mesh(cluster,setup,case_out)
        require(calls == ['inventory','mesh','inventory'], 'raw premesh check then mesh then validated inventory required')
        require([row['worker'] for row in reports] == list(range(6)), 'mesh must assign six endpoint ranks')
        require(all(row['worker'] == initial_rank for row in native.read(case_out/'premesh.inventory.json')), 'raw premesh evidence cannot be rewritten')
        checks.append('native Cluster lifecycle passes from rank '+str(initial_rank))
    rows = copy.deepcopy(raw)
    rows[4]['runtime_identity']['actual_bdf'] = '0000:ff:00.0'
    must_fail(lambda:raw_identity_and_empty(rows,setup,premesh=True))
    checks.append('raw premesh identity still refuses wrong BDF')
    rows = copy.deepcopy(raw)
    rows[4]['used_bytes']['hbm'] = 1
    must_fail(lambda:raw_identity_and_empty(rows,setup,premesh=True))
    checks.append('raw premesh emptiness still refuses payload')
    rows = copy.deepcopy(raw)
    rows[4]['transactions'] = ['active']
    must_fail(lambda:raw_identity_and_empty(rows,setup,premesh=True))
    checks.append('raw premesh idle gate still refuses active tx')
    raise_cleanup_if_primary_succeeded(dict(failure='original failure',cleanup_failure='secondary failure'))
    checks.append('primary exception is not replaced by cleanup exception')
    must_fail(lambda:raise_cleanup_if_primary_succeeded(dict(cleanup_failure='cleanup failure')))
    checks.append('cleanup-only failure remains fatal')
    write(out/'qualification.json',dict(passed=True,checks=checks,CPU_fixture_only=True,source_actual_ref=ref(a.actual/'startup.inventory.json'),runner_ref=ref(Path(__file__).parent/'runner.py'),GPU_launches=0,remote_writes=0))
    print(str(out/'qualification.json'))

if __name__ == '__main__':
    main()
