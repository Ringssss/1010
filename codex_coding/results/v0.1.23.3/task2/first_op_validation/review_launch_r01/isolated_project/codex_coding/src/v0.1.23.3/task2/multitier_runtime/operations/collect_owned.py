"""Check exact task child absence and empty groups after coordinated STOP."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
HELPER = ROOT/'codex_coding/src/v0.1.23.3/task2/three_node_recovery/admission28/runtime_gate_r03.py'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--node',required=True)
    a = p.parse_args()
    plan = json.loads(a.plan.read_text())
    ms = importlib.util.spec_from_file_location('frozen_cleanup',HELPER)
    gate = importlib.util.module_from_spec(ms)
    ms.loader.exec_module(gate)
    reports = []
    for row in plan['processes']:
        if row['node']!=a.node:
            continue
        path = Path(row['out'])
        owner = json.loads((path/'child.owner.json').read_text())
        summary = json.loads((path/'supervisor.summary.json').read_text())
        if owner['boot_id']!=gate.boot_id() or owner['pid_namespace']!=os.readlink('/proc/self/ns/pid'):
            raise ValueError('exact owner boot/namespace changed')
        actual = gate.proc(owner['pid'])
        members = gate.group_members(owner['pgid'])
        passed = not gate.exact(actual,owner) and not members and summary['cleanup']['passed']
        reports.append({'key':row['key'],'owner':owner,'current':actual,'group':members,'passed':passed,
                        'supervisor_summary_sha256':hashlib.sha256((path/'supervisor.summary.json').read_bytes()).hexdigest()})
    print(json.dumps({'node':a.node,'reports':reports,'passed':all(r['passed'] for r in reports),'signals':[]}),flush=True)
    if not all(r['passed'] for r in reports):
        raise SystemExit(2)


if __name__=='__main__':
    main()
