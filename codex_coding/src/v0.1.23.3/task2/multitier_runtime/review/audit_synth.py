"""Execute the native handler's exact synth branch with an allocation counter."""
import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[6]
SOURCE = ROOT / 'main_lib/fluidckpt/cluster/worker.py'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    raw = SOURCE.read_bytes()
    tree = ast.parse(raw)
    branches = [node for node in ast.walk(tree) if isinstance(node, ast.If)
                and ast.unparse(node.test) == "op == 'synth'"]
    if len(branches) != 1:
        raise ValueError('native synth branch not uniquely found')
    body = copy.deepcopy(branches[0].body)
    code = compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(SOURCE), 'exec')
    checks = []
    for tp_size in (False, True, 0, -1, 2.0, '4', None):
        checks.append(('invalid_size_' + repr(tp_size), dict(tp_size=tp_size, tp=0), False))
    for tp in (False, True, -1, 4, 0.0, '0', None):
        checks.append(('invalid_rank_' + repr(tp), dict(tp_size=4, tp=tp), False))
    checks.extend([('default_rank0', dict(tp=0), True), ('default_rank1', dict(tp=1), True),
                   ('default_rank2_rejected', dict(tp=2), False),
                   ('tp4_rank2', dict(tp_size=4, tp=2), True), ('tp4_rank3', dict(tp_size=4, tp=3), True)])
    rows = []
    for name, values, accepted in checks:
        calls, shards = [], {}
        def synth(snap, tp, nbytes, seed):
            calls.append(dict(snap=snap, tp=tp, nbytes=nbytes, seed=seed))
            shards[snap, tp] = SimpleNamespace(meta=dict(tp_rank=tp, synthetic=True))
            return dict(GiB=nbytes / 2**30)
        worker = SimpleNamespace(synth=synth, shards=shards)
        payload = dict(snap='audit', nbytes=8 << 20, seed=4, **values)
        env = dict(worker=worker, body=payload)
        error = None
        try:
            exec(code, env)
        except ValueError as exc:
            error = str(exc)
        actual_accept = error is None
        passed = actual_accept == accepted and len(calls) == int(accepted)
        if accepted:
            result = env['result']
            meta = shards['audit', values['tp']].meta
            expected_size = values.get('tp_size', 2)
            passed = passed and result['synthetic'] is True and result['tp_size'] == expected_size
            passed = passed and result['tp_rank'] == values['tp'] and meta['tp_size'] == expected_size
            passed = passed and meta['tp_rank'] == values['tp'] and meta['synthetic'] is True
        rows.append(dict(name=name, passed=passed, allocation_calls=len(calls), error=error))
    (args.out / 'worker.source.py').write_bytes(raw)
    report = dict(status='passed' if all(row['passed'] for row in rows) else 'failed',
                  scope='CPU execution of exact native synth handler branch; no GPU/HTTP execution',
                  source_path=str(SOURCE), source_sha256=hashlib.sha256(raw).hexdigest(),
                  checks=rows, GPU_calls=0, remote_writes=0)
    (args.out / 'qualification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(status=report['status'], checks=len(rows), source_sha256=report['source_sha256'])))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
