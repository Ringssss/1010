"""Audit the append-only package and isolated entrypoint dependency closure."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[6]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--cpu', type=Path, required=True)
    parser.add_argument('--cpu-b', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.prepared = args.prepared.resolve()
    args.cpu = args.cpu.resolve()
    args.cpu_b = args.cpu_b.resolve()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    checks, refs = [], []
    def digest(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    def read(path):
        refs.append({'path':str(path.resolve()),'sha256':digest(path)})
        return json.loads(path.read_text())
    def check(name, passed):
        checks.append({'name':name,'passed':bool(passed)})
    plan = read(args.prepared/'launch_plan.json')
    prior = read(Path(plan['prior_plan_ref']['path']))
    check('exact prior plan freeze', digest(plan['prior_plan_ref']['path']) == plan['prior_plan_ref']['sha256'])
    for field in ('workers','ssd','lifetime_s','startup_s','aggregate_budget','frozen_model_ref'):
        check('same previous finite resource contract: '+field, plan[field] == prior[field])
    check('old bounded model unchanged and unused', digest(plan['frozen_model_ref']['path'])
          == plan['frozen_model_ref']['sha256'] == 'e98bbed04d5ce502439acea4fd8fabd554dd67e69ca2b94ebba784ab8535734f'
          and plan['model_consumed'] is False)
    check('A exact four-cell order/eight requests', plan['experiment_stage'] == 'A'
          and plan['protocol']['cell_order'] == [1,4,3,2] and plan['protocol']['requests'] == 8)
    package = read(args.prepared/'append_assets.json')
    check('exact eight newly frozen implementation files', len(package['assets']) == 8)
    for item in package['assets']:
        check(Path(item['relative_path']).name + ': live/base64 SHA equals frozen', digest(ROOT/item['relative_path'])
              == item['sha256'] == hashlib.sha256(base64.b64decode(item['base64'])).hexdigest())
    for node in ('node28','node30','node27'):
        old = read(args.prepared/(node+'.prior.spec.json'))
        current = read(args.prepared/(node+'.setup.spec.json'))
        oldmap = {x['relative_path']:x['sha256'] for x in old['assets']}
        newmap = {x['relative_path']:x['sha256'] for x in current['assets']}
        check(node+': previous closure untouched, exact append only', all(newmap.get(k)==v for k,v in oldmap.items())
              and set(newmap)-set(oldmap)=={x['relative_path'] for x in package['assets']})
        check(node+': all frozen source closure matches live', all(digest(ROOT/k)==v for k,v in newmap.items()))
    for row in plan['processes']:
        old = next(x for x in prior['processes'] if x['key']==row['key'])
        if row['kind']=='worker':
            check(row['key']+': preserved topology input; distinct owned dump',
                  row['env'].get('NCCL_TOPO_FILE')==old['env'].get('NCCL_TOPO_FILE')
                  and row['env'].get('NCCL_TOPO_DUMP_FILE') != row['env'].get('NCCL_TOPO_FILE'))
            check(row['key']+': only w4 changes worker implementation',
                  row['argv'][1].endswith('/first_op_validation/execution/worker.py') if row['worker']==4
                  else row['argv'][1]==old['argv'][1])
    for path, stage in ((args.cpu,'A'),(args.cpu_b,'B')):
        qualification = read(path/'qualification.json')
        check(stage+': real CPU primitive chain and no GPU/remote write', qualification['passed']
              and qualification['CPU_REPLAY_ONLY'] and qualification['GPU_launches']==qualification['actual_GPU_measurements']
              == qualification['remote_writes']==0 and qualification['simulated_requests']==8)
        check(stage+': CPU physical208/logical80MiB ledgers bootstrap separate', qualification['physical_copy_bytes']==208<<20
              and qualification['logical_SHA_bytes']==80<<20)
        trace = read(path/'tensor_operation_trace.json')
        copies = [x for x in trace if x['event']=='copy']
        check(stage+': actual16 measured+8 prepare copies from trace', len([x for x in copies if x['name'].startswith('firstop-executor')])==16
              and len([x for x in copies if x['name'].startswith('firstop-prepare')])==8)
        check(stage+': actual first task not warmed and no early metadata', all(
            [x['event'] for x in trace if x['name']==name][:1] == ['set_device']
            and all(sum(x['event']=='copy' for x in trace[:i] if x['name']==name)>=2
                    for i,t in enumerate(trace) if t['name']==name and t['event']=='metadata')
            for name in {x['name'] for x in copies if x['name'].startswith('firstop-executor')}))
        transitions = read(path/'state_trace.json')
        check(stage+': normal PREPARED→FILLED→READY→finish chain, no phase reset', all(
            all(phase==expected for own in x['phases'].values() for phase in own.values())
            for x in transitions for expected in ({'prepare':'PREPARED','execute':'FILLED','complete':'READY'}.get(x['operation']),)
            if expected is not None))

    closure = args.output/'isolated_project'
    closure.mkdir()
    spec = read(args.prepared/'node27.setup.spec.json')
    for asset in spec['assets']:
        target = closure/asset['relative_path']
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/asset['relative_path'],target)
    commands = []
    execution = closure/'codex_coding/src/v0.1.23.3/task2/first_op_validation/execution'
    wrapper = "import sys,runpy; from unittest.mock import MagicMock; sys.modules['torch']=MagicMock(); p=sys.argv[1]; sys.path.insert(0,str(__import__('pathlib').Path(p).parent)); sys.argv=[p,'--help']; runpy.run_path(p,run_name='__main__')"
    for entry in ('worker.py',):
        command = [sys.executable,'-I','-c',wrapper,str(execution/entry)]
        proc = subprocess.run(command,cwd=closure,capture_output=True,text=True,timeout=20)
        commands.append({'entry':entry,'command':command,'returncode':proc.returncode,'stdout':proc.stdout,'stderr':proc.stderr})
        check(entry+': full package isolated entry import/CLI with only torch stub', proc.returncode==0 and 'usage:' in proc.stdout)
    for entry in ('runner.py','prepare.py','session.py','cpu_replay.py'):
        command = [sys.executable,str(ROOT/'codex_coding/src/v0.1.23.3/task2/first_op_validation/execution'/entry),'--help']
        proc = subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=20)
        commands.append({'entry':entry,'execution_role':'local_controller_only','command':command,'returncode':proc.returncode,
                         'stdout':proc.stdout,'stderr':proc.stderr})
        check(entry+': local controller entry uses local complete source closure', proc.returncode==0 and 'usage:' in proc.stdout)
    mode = execution/'mode_file.py'
    target = args.output/'isolated_mode/first_op_validation/first_op_runtime_review.inventory_mode.json'
    command = [sys.executable,'-I',str(mode),'--path',str(target)]
    proc = subprocess.run(command,cwd=closure,capture_output=True,text=True,timeout=10)
    commands.append({'entry':'mode_file.py','command':command,'returncode':proc.returncode,'stdout':proc.stdout,'stderr':proc.stderr})
    check('real stdlib mode-file CLI, no torch stub', proc.returncode==0 and json.loads(target.read_text())=={'mode':'cached'})
    duplicate = subprocess.run(command,cwd=closure,capture_output=True,text=True,timeout=10)
    check('mode-file duplicate refuses overwrite', duplicate.returncode!=0 and 'FileExistsError' in duplicate.stderr)
    result = {'status':'launch_cpu_and_source_closure_passed' if all(x['passed'] for x in checks) else 'launch_cpu_or_source_closure_failed',
              'checks':checks,'refs':refs,'GPU_launches':0,'remote_calls':0,
              'scope':'New package and CPU qualification; fresh runtime physical admission remains a main-agent action. Isolated remote worker CLI stubs only torch, not product dependencies; runner/prepare/session/cpu_replay are local controller entries and use the local source closure.'}
    (args.output/'isolated_commands.json').write_text(json.dumps(commands,indent=2)+'\n')
    (args.output/'qualification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'checks':len(checks),'failed':[x['name'] for x in checks if not x['passed']]}))
    if not all(x['passed'] for x in checks):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
