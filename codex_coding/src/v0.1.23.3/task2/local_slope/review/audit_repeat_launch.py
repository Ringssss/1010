"""Check the final two-request same-handler diagnostic contract without GPU calls."""
import argparse
import ast
import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--prepared', required=True, type=Path)
    p.add_argument('--cpu', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    refs, checks = [], []
    def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
    def read(path):
        refs.append({'path': str(path.resolve()), 'sha256': sha(path)})
        return json.loads(path.read_text())
    def check(name, ok):
        checks.append({'name': name, 'passed': bool(ok)})
        assert ok, name
    plan = read(a.prepared / 'launch_plan.json')
    old_path = Path(plan['prior_plan_ref']['path'])
    old = read(old_path)
    package = read(a.prepared / 'append_assets.json')
    check('predecessor exact SHA', sha(old_path) == plan['prior_plan_ref']['sha256'])
    for key in ('workers','ssd','lifetime_s','startup_s','aggregate_budget'):
        check('unchanged ' + key, plan[key] == old[key])
    protocol = plan['protocol']
    check('exactly two logical requests4/16MiB', [c['nbytes'] for c in protocol['cases']] == [4 << 20, 16 << 20])
    check('two copies/one source/one target per request', protocol['copy_rounds_per_request'] == 2
          and protocol['source_worker'] == protocol['target_worker'] == 4 and protocol['source_bytes'] == 16 << 20)
    check('no fit/model update/further request', protocol['fits'] == protocol['model_updates'] == protocol['further_GPU_requests'] == 0)
    check('metadata outside both rounds/no events', 'after both' in protocol['metadata_policy'] and 'no CUDA events' in protocol['event_policy'])
    check('old model remains unconsumed', plan['model_consumed'] is False
          and sha(Path(plan['frozen_model_ref']['path'])) == plan['frozen_model_ref']['sha256']
          == 'e98bbed04d5ce502439acea4fd8fabd554dd67e69ca2b94ebba784ab8535734f')
    check('only four new source assets', len(package['assets']) == 4)
    for asset in package['assets']:
        path = ROOT / asset['relative_path']
        check(path.name + ': live source/base64 freeze match', sha(path) == asset['sha256']
              == hashlib.sha256(base64.b64decode(asset['base64'])).hexdigest())
        refs.append({'path': str(path), 'sha256': sha(path)})
        ast.parse(path.read_text())
    for node in ('node28','node30','node27'):
        prior = read(a.prepared / (node + '.prior.spec.json'))
        current = read(a.prepared / (node + '.setup.spec.json'))
        original = read(old_path.parent / (node + '.setup.spec.json'))
        check(node + ': actual prior equals completed first session source', prior == original)
        before = {r['relative_path']:r for r in prior['assets']}
        after = {r['relative_path']:r for r in current['assets']}
        check(node + ': every prior asset unchanged', all(after[k] == v for k,v in before.items()))
        check(node + ': only append four sources', set(after)-set(before) == {r['relative_path'] for r in package['assets']})
    check('only w4 uses repeat overlay', all(p['argv'][1].endswith('/repeat_worker.py') if p['worker'] == 4
          else p['argv'][1] == next(q['argv'][1] for q in old['processes'] if q['key'] == p['key'])
          for p in plan['processes'] if p['kind'] == 'worker'))
    cpu = read(a.cpu / 'qualification.json')
    check('full CPU replay passed and GPU zero', cpu['passed'] and cpu['CPU_REPLAY_ONLY']
          and cpu['GPU_launches'] == cpu['actual_GPU_measurements'] == cpu['remote_writes'] == 0 and cpu['simulated_requests'] == 2)
    check('CPU byte accounting bootstrap+logical36MiB physical56MiB', cpu['logical_SHA_bytes'] == 36 << 20 and cpu['physical_copy_bytes'] == 56 << 20)
    rows = read(a.cpu / 'measure/measurements.json')
    check('CPU actual case objects match frozen protocol', [r['case'] for r in rows] == protocol['cases'])
    for row in rows:
        record = row['flow']['execution'][4]['records'][0]
        diag = record['local_slope_diagnosis']
        check(row['case']['label'] + ': exactly2n physical/n logical copy bytes', record['actual_copy_bytes'] == 2*record['bytes']
              and record['logical_SHA_bytes'] == record['bytes'] and len(diag['rounds']) == 2)
        check(row['case']['label'] + ': one handler same pointers/no events', len({r['native_tid'] for r in diag['rounds']}) == 1
              and diag['same_source_view_address'] and diag['same_target_view_address'] and diag['CUDA_events'] == 0)
    (a.output / 'qualification.json').write_text(json.dumps({'status':'repeat_launch_cpu_evidence_passed', 'checks':checks,
        'refs':refs, 'GPU_launches':0, 'remote_calls':0, 'scope':'CPU lifecycle/append-only two-request qualification; GPU timings unmeasured.'}, indent=2)+'\n')
    print(json.dumps({'checks':len(checks),'refs':len(refs),'status':'repeat_launch_cpu_evidence_passed'}))


if __name__ == '__main__':
    main()
