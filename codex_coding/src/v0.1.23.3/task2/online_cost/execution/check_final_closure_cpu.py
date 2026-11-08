"""CPU actual-ledger replay, refusal cases and direct-child terminal wait proof."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from types import SimpleNamespace
from unittest.mock import patch

import derived_supervisor as supervisor


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    base=Path(__file__).resolve().parents[6]/'codex_coding/results/v0.1.23.3/task2/online_cost'
    out=a.out.resolve()
    if out.parent!=base or not out.name.startswith('execution_host_final_closure_cpu_'):
        raise ValueError('new owned CPU result required')
    out.mkdir(exist_ok=False);checks=[]
    def check(name,test):checks.append(dict(name=name,passed=bool(test)))
    spec=importlib.util.spec_from_file_location('frozen_owner_gate',supervisor.HELPER)
    actual_gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(actual_gate)
    archive=base/'execution_host_local_pause_run_r01/collection_node27.tar'
    archive_sha=hashlib.sha256(archive.read_bytes()).hexdigest()
    with tarfile.open(archive) as stream:
        engine=json.load(stream.extractfile('operations_process_engine_node27/supervisor.summary.json'))
        owner=json.load(stream.extractfile('operations_process_engine_node27/child.owner.json'))
    original=copy.deepcopy(engine['cleanup'])
    reaping=dict(engine['adopted_child_reap'],waitpid_echild=True)
    # The old reaper's passed branch was reached only on ChildProcessError.
    state=dict(boot=engine['boot_id'],namespace=engine['pid_namespace'],
               leader=dict(pid=owner['pid'],live=False,state='absent'),members=[])
    gate=SimpleNamespace(IDENTITY=actual_gate.IDENTITY,exact=actual_gate.exact,
                        boot_id=lambda:state['boot'],proc=lambda pid:state['leader'],
                        group_members=lambda pgid:state['members'])
    def replay(clean=original,reap=reaping,registered=owner):
        with patch.object(supervisor.os,'readlink',return_value=state['namespace']),patch.object(supervisor.os,'killpg',side_effect=AssertionError('no closure signal')):
            return supervisor.final_owned_closure(gate,owner['pid'],registered,engine['boot_id'],engine['pid_namespace'],clean,reap)
    row=replay();check('actual_engine_exit_race_final_ledger_qualified',row['passed'] and row['guard_exit_race_reconciled'] and row['registered_leader_exitcode']==-9)
    check('original_failed_guard_unchanged',original==engine['cleanup'] and not engine['passed'])
    for key,value,label in (
            ('leader',dict(owner,start_time='PID_REUSED',live=True),'reject_reused_leader'),
            ('members',[dict(owner)],'reject_remaining_owned_group'),
            ('boot','different_boot','reject_changed_boot'),
            ('namespace','different_namespace','reject_changed_namespace')):
        prior=state[key];state[key]=value;check(label,not replay()['passed']);state[key]=prior
    check('reject_missing_terminal_original_leader',not replay(reap=dict(reaping,reaped=[]))['passed'])
    check('reject_other_reaped_PID_as_proof',not replay(reap=dict(reaping,reaped=[dict(pid=owner['pid']+1,wait_status=9)]))['passed'])
    check('reject_no_ECHILD_proof',not replay(reap=dict(reaping,waitpid_echild=False))['passed'])
    check('reject_pending_adopted_children',not replay(reap=dict(reaping,passed=False,pending_children=['999']))['passed'])
    check('reject_different_cleanup_failure',not replay(clean=dict(original,reason='exact owner/session mismatch; no signal'))['passed'])
    check('reject_wrong_presignal_start',not replay(clean=dict(original,before=dict(original['before'],start_time='wrong')))['passed'])
    check('reject_incomplete_owner',not replay(registered={'pid':owner['pid']})['passed'])
    supervisor.enable_subreaper()
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(.1)'],start_new_session=True)
    registered=actual_gate.proc(child.pid)
    real_reap=supervisor.reap_adopted_children(timeout_s=2)
    real=supervisor.final_owned_closure(actual_gate,child.pid,registered,actual_gate.boot_id(),os.readlink('/proc/self/ns/pid'),
                                      dict(passed=False,before=registered,reason='identity changed before KILL; no further signal'),real_reap)
    check('real_registered_direct_child_waitpid_and_ECHILD',real['passed'] and real['registered_leader_exitcode']==0)
    check('original_archive_immutable',hashlib.sha256(archive.read_bytes()).hexdigest()==archive_sha)
    provenance=json.loads(Path(__file__).with_name('supervisor_provenance.json').read_text())
    check('future_supervisor_provenance_hash_matches',provenance['derived_sha256']==hashlib.sha256(Path(supervisor.__file__).read_bytes()).hexdigest())
    result=dict(passed=all(c['passed'] for c in checks),checks=checks,CPU_REPLAY_AND_REAL_CHILD=True,
                GPU_launches=0,SSH_commands=0,signals_sent=0,
                original_archive_ref=dict(path=str(archive),sha256=archive_sha),actual_ledger_replay=row,real_child_closure=real)
    (out/'qualification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(passed=result['passed'],checks=len(checks),out=str(out))))
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
