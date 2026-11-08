"""CPU-only synthetic gate and rank-zero prior-build construction checks."""
import argparse
import copy
import sys
from common import ROOT,HERE,RESULTS,load,native,write,ref,fresh_output
import gate
import prepare
def main():
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--cpu-run',required=True)
    args=p.parse_args();out=fresh_output(args.out)
    analysis=load('cost_ledger_CPU_gate_test_analysis',ROOT/'codex_coding/src/v0.1.23.3/task2/cost_ledger/analysis/analyze.py')
    cpu=native.read(args.cpu_run+'/measure/stage_measurements.json');cpu['synthetic_protocol_fixture_only']=True
    cpu['session_id']='cost_ledger_runtime_C1_synthetic_protocol_cpu'
    for sample in cpu['samples']:
        sample['session_id']=cpu['session_id'];actual=sample['actual']
        service=int(100000+10000*actual['N_mib']+100000*actual['K']+100000*len(actual['chunk_mib']));worker=service+100000
        sample['measured'].update(service_ns=service,worker_ns=worker,controller_execute_ns=worker+500000,full_filled_ns=worker+1500000)
    cpu['observer'].update(clock_extra_p95_ns=500,dummy_log_scale_ns=2000)
    write(out/'synthetic_CPU_ONLY_calibration.json',cpu);bref=ref(out/'synthetic_CPU_ONLY_calibration.json')
    pre=native.read(args.cpu_run+'/predeclared_protocol.json');frozen=analysis.encoded(analysis.calibrate(cpu,pre,bref))
    assert frozen['holdout_authorized_by_analysis']
    write(out/'synthetic_CPU_ONLY_frozen.json',frozen);fref=ref(out/'synthetic_CPU_ONLY_frozen.json');checks=[]
    def check(name,value):
        assert value,name
        checks.append(dict(name=name,passed=True))
    check('CPU_only_freeze_exact_reconstruction',gate.prediction_contract(fref,'cost_ledger_runtime_C2_synthetic_protocol_cpu',cpu_only=True)==frozen)
    def rejection(name,reference,session,message,**kw):
        try:gate.prediction_contract(reference,session,**kw)
        except ValueError as exc:check(name,message in str(exc))
        else:raise AssertionError(name)
    rejection('CPU_freeze_cannot_authorize_GPU',fref,'cost_ledger_runtime_C2_gpu_forbidden','CPU replay cannot authorize GPU')
    changed=copy.deepcopy(frozen);changed['holdout_authorized_by_analysis']=False;write(out/'C1_failed_gate_fixture.json',changed)
    rejection('C1_failed_gate_stop',ref(out/'C1_failed_gate_fixture.json'),'cost_ledger_runtime_C2_fail','C1 gate failed',cpu_only=True)
    changed=copy.deepcopy(frozen);changed['outer']['controller_residual_ns']['value']+=1;write(out/'tampered_prediction_fixture.json',changed)
    rejection('tampered_prediction_stop',ref(out/'tampered_prediction_fixture.json'),'cost_ledger_runtime_C2_tamper','independently replayed C1',cpu_only=True)
    rejection('same_session_stop',fref,cpu['session_id'],'new session',cpu_only=True)
    # Only build construction swaps the gate for the explicitly CPU-only replay.
    # The generated launch still points to a CPU artifact and is rejected by the real GPU gate.
    original_argv=sys.argv;original_gate=prepare.prediction_contract
    c1=RESULTS/(out.name+'_build_C1');c2=RESULTS/(out.name+'_build_C2')
    try:
        sys.argv=['prepare.py','build','--out',str(c1),'--session','cost_ledger_runtime_C1_build_cpu_'+out.name,
            '--experiment-stage','C1','--predeclared',args.cpu_run+'/predeclared_protocol.json'];prepare.main()
        prepare.prediction_contract=lambda r,s,**kw:gate.prediction_contract(r,s,cpu_only=True)
        write(out/'CPU_ONLY_review_fixture.json',dict(passed=True,CPU_REPLAY_ONLY=True,synthetic_protocol_fixture_only=True))
        sys.argv=['prepare.py','build','--out',str(c2),'--session','cost_ledger_runtime_C2_build_cpu_'+out.name,
            '--experiment-stage','C2','--predeclared',args.cpu_run+'/predeclared_protocol.json','--prior-prepared',str(c1),
            '--prediction',fref['path'],'--calibration-review',str(out/'CPU_ONLY_review_fixture.json')];prepare.main()
    finally:prepare.prediction_contract=original_gate;sys.argv=original_argv
    p1=native.read(c1/'launch_plan.json');p2=native.read(c2/'launch_plan.json')
    check('C1_single_worker_world1',len(p1['workers'])==len(p1['processes'])==1 and p1['workers'][0]['worker']==0)
    check('C2_prior_rank0_build',len(p2['processes'])==1 and p2['processes'][0]['worker']==0 and p2['prior_setup_directory']==str(c1))
    check('master_port_admission',29981 in native.read(c2/'node27.setup.spec.json')['ports'])
    check('only_gpu0_role',[r['gpu'] for r in native.read(c2/'node27.setup.spec.json')['roles']]==[0])
    rejection('CPU_test_launch_still_GPU_rejected',p2['prediction_ref'],p2['session'],'CPU replay cannot authorize GPU',review_reference=p2['calibration_review_ref'])
    write(out/'qualification.json',dict(passed=True,checks=checks,CPU_REPLAY_ONLY=True,synthetic_protocol_fixture_only=True,
        GPU_launches=0,remote_calls=0,C1_build_ref=ref(c1/'launch_plan.json'),C2_build_ref=ref(c2/'launch_plan.json'),
        hardware_admission=False,fit_is_synthetic_not_measured=True))
    print(len(checks),'checks passed')
if __name__=='__main__':main()
