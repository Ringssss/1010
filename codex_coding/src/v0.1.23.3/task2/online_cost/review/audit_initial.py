"""Read local historical live runs and freeze the online-readiness source anchors."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
OUT_ROOT = ROOT / 'codex_coding/results/v0.1.23.3/task2/online_cost'
ANCHORS = {
    'main_lib/fluidckpt/serving/lifecycle.py': ['def resume', "'load_begin'", 'materialize(runner.model', 'export_storages', '_wait_done', 'torch.cuda.synchronize()', 'torch.distributed.barrier', "'weights_ready'"],
    'main_lib/fluidckpt/serving/measure.py': ['def summarize', 'cross-host monotonic', 'max(r['],
    'main_lib/fluidckpt/serving/keeper.py': ['class HbmTarget', 'metadata_compare', 'index_validation', 'def scatter', 'def done'],
    'main_lib/fluidckpt/serving/sglang_hook.py': ['def export_storages', 'def _wait_done', 'time.sleep(0.005)'],
    'main_lib/fluidckpt/cluster/catalog.py': ['def publish', 'def acquire', 'def release'],
    'main_lib/fluidckpt/cluster/planning.py': ['def validate_plan', 'incomplete target coverage'],
    'main_lib/fluidckpt/cluster/control.py': ['def transfer', 'verified = self.verify', 'actual target bytes', 'def finish'],
    'main_lib/fluidckpt/cluster/worker.py': ['def prepare', 'self.catalog.acquire', 'torch.cuda.synchronize(self.dev)', 'def complete', 'new replicas require complete content verification', 'target.done'],
    'codex_coding/src/v0.1.23.3/task2/basic_scenarios/run_scenarios.py': ['def start_resume', 'def run_restore', 'stamp =', 'verify_before_ready=False', 'post_ready_verification_s', 'self.cluster.finish'],
    'codex_coding/src/v0.1.23.3/task2/multitier_runtime/operations/host_readback.py': ['gpu_api_calls=0', "if spec['node'] in ('node28', 'node30')", "root = Path('/sys/bus/pci/devices')"],
    'codex_coding/src/v0.1.23.3/task2/multitier_runtime/operations/control.py': ['def readback', 'def bind', "choices=['readonly'"],
    'codex_coding/src/v0.1.23.3/task2/bounded_cost/execution/common.py': ['def quiet_readback'],
    'codex_coding/src/v0.1.23.3/task2/cost_ledger/execution_gpu4/prepare.py': ["a.mode=='metadata'", "'node27:4-4'", "0000:85:00.0"],
    'codex_coding/src/v0.1.23.3/task2/basic_scenarios/inspect_runtime.py': ['hy-smi', 'def inspect'],
}


def ref(path):
    raw = path.read_bytes()
    return {'path': str(path.relative_to(ROOT)), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def audit_case(case, model):
    flow = case['flow']
    ops = flow['plan']['ops']
    targets = flow['plan']['targets']
    assert all(t['mode'] == 'live' for t in targets), 'historical case is not all-live'
    source_manifest = {s['tp']: s for s in model['sources']}
    assert len(targets) == len(source_manifest) == 2
    for target in targets:
        source = source_manifest[target['tp']]
        assert target['identity'] == source['identity'] and target['n'] == source['n'] and target['off'] == 0
        cursor = target['off']
        for op in sorted((o for o in ops if o['target'] == target['key']), key=lambda o: o['off']):
            assert op['off'] == cursor and op['n'] > 0 and op['identity'] == target['identity'] and op['tp'] == target['tp']
            cursor += op['n']
        assert cursor == target['off'] + target['n'], 'incomplete coverage'
    target_counts = {}
    for worker in flow['execution']:
        wid = worker['worker']
        expected = [o['op_id'] for o in ops if wid in (o['src_w'], o['dst_w'])]
        assert [r['op_id'] for r in worker['records']] == expected
        for record in worker['records']:
            op = next(o for o in ops if o['op_id'] == record['op_id'])
            assert record['bytes'] == op['n']
            if record['role'] in ('target', 'both'):
                assert record['op_id'] not in target_counts
                target_counts[record['op_id']] = record['bytes']
    assert target_counts == {o['op_id']: o['n'] for o in ops}
    verification = case['verification']
    assert verification['passed'] and verification['checked_bytes'] == sum(o['n'] for o in ops)
    for op in ops:
        row = verification['operations'][str(op['op_id'])]
        assert row['n'] == op['n'] and row['source_sha256'] == row['target_sha256'] and len(row['source_sha256']) == 64
    ranks = case['timing']['ranks']
    begins, ends, hosts = [], [], set()
    for rank, data in ranks.items():
        events = data['events']
        begin = [e for e in events if e['event'] == 'load_begin']
        end = [e for e in events if e['event'] == 'weights_ready']
        assert len(begin) == len(end) == 1 and begin[0]['rank'] == end[0]['rank'] == int(rank)
        assert not any(e['event'] == 'load_failed' for e in events)
        assert end[0]['monotonic_ns'] >= begin[0]['monotonic_ns']
        hosts.update(e['host'] for e in events)
        begins.append(begin[0]['monotonic_ns']); ends.append(end[0]['monotonic_ns'])
    assert len(hosts) == 1 and len(ranks) == 2
    actual = (max(ends) - min(begins)) / 1e9
    assert abs(actual - case['timing']['load_s']) < 1e-9
    assert case['correct'] is True and case['status'] == 'passed'
    assert model['reference']['tokens'] == 32 and case['reply']['text'] == model['reference']['text']
    if 'tokens' in case['reply']:
        assert case['reply']['tokens'] == 32
    return dict(phase=case['phase'], target_host=next(iter(hosts)), logical_bytes=sum(o['n'] for o in ops),
                operations=len(ops), T_ready_s=actual, request_to_ready_ack_s=case.get('request_to_ready_ack_s'),
                post_ready_verification_s=case.get('post_ready_verification_s'),
                ordered_records_and_full_byte_SHA_passed=True, exact_reference_output_passed=True,
                evidence_class='historical_reference', new_performance_samples=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    assert out.parent == OUT_ROOT and out.name.startswith('review_')
    assert not out.exists(), 'output must be fresh'
    sources, anchors, historical = [ref(Path(__file__))], {}, []
    for filename, patterns in ANCHORS.items():
        path = ROOT / filename
        sources.append(ref(path))
        lines = path.read_text().splitlines()
        anchors[filename] = [{'pattern': pattern, 'lines': [i + 1 for i, line in enumerate(lines) if pattern in line]} for pattern in patterns]
    for filename in ['basic_scenarios/s2_r01/summary.json', 'basic_scenarios/s3_r01/summary.json', 'paper_e1_e8/e3/e3_c02_qwen_r01/summary.json']:
        path = ROOT / 'codex_coding/results/v0.1.23.3/task2' / filename
        before = ref(path)
        body = json.loads(path.read_text())
        assert body['status'] == 'passed'
        session_path = ROOT / ('codex_coding/results/v0.1.23.3/task2/basic_scenarios/qwen_bootstrap_r01/session.json'
                               if filename.startswith('basic_scenarios/') else body['protocol']['session'])
        session_ref = ref(session_path)
        model = json.loads(session_path.read_text())['models']['B']
        historical.extend(dict(run=filename, **audit_case(case, model)) for case in body['cases'])
        assert ref(session_path) == session_ref
        assert ref(path) == before
        sources.extend([before, session_ref])
    result = dict(status='passed_static_and_historical_audit', development='v0.1.23.3/task2',
        GPU_launches=0, SSH_commands=0, new_performance_samples=0, online_prediction_qualified=False,
        primary_gate=dict(metric='whole_online_weights_ready', max_relative_error=0.10, per_scenario=True,
                          per_term_thresholds_required=False, complexity_upgrade_gate_required=False),
        historical_live_cases=historical, source_anchors=anchors,
        unresolved=['fresh GPU4/5 mapping and capacity', 'real model/source placement and budgets',
                    'final measured start boundary', 'finite representative scenario calibration and held-out protocol'])
    out.mkdir(parents=True)
    (out / 'initial_audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    (out / 'sources.json').write_text(json.dumps(sources, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(status=result['status'], historical_cases=len(historical), out=str(out), GPU_launches=0, SSH_commands=0)))


if __name__ == '__main__':
    main()
