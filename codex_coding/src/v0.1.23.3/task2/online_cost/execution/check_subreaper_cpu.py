"""CPU-only real fork/grandchild adoption and reaping qualification."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[5]
RESULTS=ROOT/'codex_coding/results/v0.1.23.3/task2/online_cost'
loaded=importlib.util.spec_from_file_location('owned_subreaper',HERE/'derived_supervisor.py');module=importlib.util.module_from_spec(loaded);loaded.loader.exec_module(module)


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();out=a.out.resolve()
    if out.parent!=RESULTS or not out.name.startswith('execution_host_subreaper_cpu_'):raise ValueError('new owned subreaper CPU output required')
    out.mkdir(exist_ok=False)
    enabled=module.enable_subreaper()
    code="import os,sys,time\npid=os.fork()\nif pid==0:\n print(os.getpid(),flush=True)\n time.sleep(.3)\n os._exit(0)\ntime.sleep(.05)\nos._exit(0)\n"
    child=subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE,text=True,start_new_session=True)
    grandchild=int(child.stdout.readline());child.wait(timeout=2)
    stat=Path('/proc',str(grandchild),'stat').read_text().rsplit(') ',1)[1].split()
    adopted=int(stat[1])==os.getpid()
    reaped=module.reap_adopted_children(timeout_s=2)
    checks=[dict(name='subreaper_enabled_before_real_child_spawn',passed=enabled),dict(name='real_orphan_grandchild_adopted_by_task_supervisor',passed=adopted),dict(name='original_child_reaped',passed=child.returncode==0),dict(name='adopted_grandchild_waitpid_recorded',passed=any(r['pid']==grandchild for r in reaped['reaped'])),dict(name='no_adopted_orphan_remaining',passed=reaped['passed'] and not Path('/proc',str(grandchild)).exists())]
    result=dict(CPU_REAL_FORK=True,GPU_launches=0,SSH_commands=0,checks=checks,passed=all(c['passed'] for c in checks),adopted_child_reap=reaped)
    (out/'qualification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(dict(passed=result['passed'],checks=len(checks),out=str(out))))
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
