"""CPU-only all-node collection regression check; quiet failures stay failures."""
import argparse
import json
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from orchestrate import HostOps,RESULTS


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();out=a.out.resolve()
    if out.parent!=RESULTS or not out.name.startswith('execution_host_collection_cpu_'):raise ValueError('new owned collection CPU output required')
    out.mkdir(exist_ok=False)
    ops=HostOps.__new__(HostOps);ops.out=out;ops.events=0;ops.deadline_at=None;ops.specs={'node27':{},'node30':{}}
    identities={n:dict(container_id=n,container_started_at='CPU',boot_id='CPU') for n in ops.specs}
    plan=dict(handoff='/CPU_FIXTURE/runtime/handoff',nodes=identities);calls=[]
    def record(node,argv,name,timeout,payload=None):calls.append(('archive',node));return b'CPU_FIXTURE_ARCHIVE'
    def host(node,name):
        calls.append(('metadata',node));body=dict(owned_cgroup=dict(fields={'pids.current':'7' if node=='node27' else '1'}))
        directory=out/name;directory.mkdir();(directory/'stdout.json').write_text(json.dumps(body));return body
    ops.record=record;ops.host=host;ops.identity=lambda node,body:identities[node]
    failed=False
    try:ops.collect(plan)
    except ValueError:failed=True
    result=json.loads((out/'collection_qualification.json').read_text())
    checks=[dict(name='both_node_archives_and_metadata_collected_after_first_quiet_failure',passed=calls==[('archive','node27'),('metadata','node27'),('archive','node30'),('metadata','node30')]),dict(name='both_raw_archives_preserved',passed=all((out/('collection_'+n+'.tar')).exists() for n in ops.specs)),dict(name='node27_quiet_failure_qualified_false',passed=not result['nodes']['node27']['passed'] and result['nodes']['node27']['pids_current']=='7'),dict(name='node30_qualified_independently',passed=result['nodes']['node30']['passed']),dict(name='overall_quiet_gate_not_weakened',passed=failed and not result['passed'])]
    qualification=dict(CPU_FIXTURE_ONLY=True,checks=checks,passed=all(c['passed'] for c in checks),GPU_launches=0,SSH_commands=0)
    (out/'qualification.json').write_text(json.dumps(qualification,indent=2)+'\n');print(json.dumps(dict(passed=qualification['passed'],checks=len(checks),out=str(out))))
    if not qualification['passed']:raise SystemExit(1)


if __name__=='__main__':main()
