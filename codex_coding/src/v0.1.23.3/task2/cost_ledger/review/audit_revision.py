"""Verify a fresh physical scope preserves the old frozen instrumentation and gates."""
import argparse
import hashlib
import json
from pathlib import Path
import runpy

from audit_core import Audit

ROOT=Path(__file__).resolve().parents[6]
BASE=ROOT/'codex_coding/src/v0.1.23.3/task2/cost_ledger'
RESULTS=ROOT/'codex_coding/results/v0.1.23.3/task2/cost_ledger'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();audit=Audit()
    old=audit.read(RESULTS/'analysis_predeclared_r03/predeclared_protocol.json')
    for reference in old['source_refs']:audit.reference(reference)
    def read_source(path):
        data=path.read_bytes();audit.refs.append({'path':str(path),'sha256':hashlib.sha256(data).hexdigest()});return data.decode()
    for name in ('worker.py','controller.py','session.py','mode_file.py'):
        audit.check('unchanged native timer/copy/process implementation: '+name,
            read_source(BASE/'execution'/name)==read_source(BASE/'execution_gpu4'/name))
    original=read_source(BASE/'analysis/analyze.py');new=read_source(BASE/'analysis_gpu4/analyze.py')
    audit.check('analysis changes exactly two physical domain literals, no hypotheses/gates/fit changes',
        original.replace('singleworker_node27_gpu0_local_native_v1','singleworker_node27_gpu4_local_native_v1')
        .replace('single_worker_node27_gpu0_world1','single_worker_node27_gpu4_world1')==new)
    audit.check('analysis rejection-path tests byte-identical',read_source(BASE/'analysis/test_analysis.py')
        ==read_source(BASE/'analysis_gpu4/test_analysis.py'))
    oldp=runpy.run_path(str(BASE/'execution/protocol.py'));newp=runpy.run_path(str(BASE/'execution_gpu4/protocol.py'))
    for key in ('SCHEMA','MIB','CASES','ORDERS','MODE','GATES'):
        audit.check('unchanged prereg experimental/numeric contract: '+key,oldp[key]==newp[key])
    oldstate=dict(oldp['STATE']);newstate=dict(newp['STATE'])
    for field in ('configuration_id','topology'):oldstate.pop(field);newstate.pop(field)
    audit.check('state changes physical domain only',oldstate==newstate)
    oldcommon=read_source(BASE/'execution/common.py');newcommon=read_source(BASE/'execution_gpu4/common.py')
    audit.check('unchanged exact budget formula, runtime inclusion, reserves, and margins',
        oldcommon[oldcommon.index("    dram=rt['cgroup_memory_current_bytes']"):]
        ==newcommon[newcommon.index("    dram=rt['cgroup_memory_current_bytes']"):])
    audit.check('new common exact GPU4/BDF85/PCI-NUMA4 admission',
        "(0,'node27',4,4)" in newcommon and "'0000:85:00.0'" in newcommon and "rt['pci_numa_node']==4" in newcommon)
    oldgate=read_source(BASE/'execution/gate.py');newgate=read_source(BASE/'execution_gpu4/gate.py')
    audit.check('same C2 gates with revised analysis source only',oldgate.replace('/cost_ledger/analysis/analyze.py',
        '/cost_ledger/analysis_gpu4/analyze.py')==newgate)
    former=audit.read(RESULTS/'review_resource_failure_C1_r01/qualification.json')
    for reference in former['refs']:audit.reference(reference)
    audit.check('GPU0 failed admission retains zero requests/no model',former['calibration_requests']==0
        and former['bootstrap_requests']==0 and former['model_identified'] is False)
    result=audit.save(args.output,'GPU4 scope-only revision and preservation of all GPU0 frozen evidence; CPU/source only',
        new_physical_scope={'node':'node27','gpu':4,'numa':4,'bdf':'0000:85:00.0','worker':0,'world_size':1},
        physical_admission=False,old_model_reused=False,additional_gpu_scan_authorized=False)
    print(json.dumps({'passed':result['passed'],'checks':len(audit.checks),'failed':[x['name'] for x in audit.checks if not x['passed']]}))
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
