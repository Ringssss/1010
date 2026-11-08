"""Inspect or unlink one exact interrupted-session synthetic SSD extent."""
import argparse
import hashlib
import json
from pathlib import Path
import stat
import subprocess
from common import ROOT, native, require, write, ref

def main():
    p=argparse.ArgumentParser();p.add_argument('--actual',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--delete',action='store_true')
    a=p.parse_args();a.actual=a.actual.resolve();a.out=a.out.resolve();require(a.actual.name=='execution_actual_r01','only interrupted r01 accepted')
    require(a.out.parent==a.actual.parent and a.out.name.startswith('execution_ssd_'),'fresh exact SSD evidence required');a.out.mkdir(parents=True,exist_ok=False)
    plan=native.read(a.actual/'runtime_setup.json');summary=native.read(a.actual/'summary.json')
    require(plan['session']=='costcal_runtime_r01' and summary['status']=='failed','known interrupted session required')
    require(all(row['returncode']==0 for row in summary['workers'].values()) and all(row['passed'] for row in summary['collection'].values()),'all old owned processes must have collected exits')
    setup_worker=plan['workers'][4];require(setup_worker['node']=='node27','exact local source SSD owner')
    require(Path('/proc/sys/kernel/random/boot_id').read_text().strip()==setup_worker['boot_id'],'boot changed')
    ci=json.loads(subprocess.run(['docker','inspect',setup_worker['container_id']],capture_output=True,check=True,timeout=5).stdout)[0]
    launch=native.read(summary['plan_ref']['path']);row=next(r for r in launch['processes'] if r['key']=='w4')
    require(ci['Id']==setup_worker['container_id'] and ci['State']['StartedAt']==row['container_started_at'] and ci['State']['Running'],'exact task container changed')
    cgraw=Path('/proc/'+str(ci['State']['Pid'])+'/cgroup').read_text();rel=next(l.split(':',2)[2] for l in cgraw.splitlines() if l.startswith('0::'));cg=Path('/sys/fs/cgroup')/rel.lstrip('/')
    require((cg/'pids.current').read_text().strip()=='1','task container not quiet')
    rid='costcal_runtime_r01/source2/ssd';journal=native.read(a.actual/'measure/journal.json');owned=next(r for r in journal['owned'] if r['id']==rid)
    require(owned['worker']==4 and owned['tier']=='ssd' and owned['n']==16 << 20,'owned extent does not match registered plan')
    bootstrap=native.read(a.actual/'measure/bootstrap2.json');require(bootstrap['verification']['passed'] and bootstrap['verification']['checked_bytes']==16 << 20,'bootstrap full SHA missing')
    expected=bootstrap['verification']['operations']['0']['target_sha256'];require(expected==bootstrap['verification']['operations']['0']['source_sha256'],'bootstrap SHA mismatch')
    root=Path(plan['ssd']['n2']['directory']);require(root.is_dir() and not root.is_symlink(),'unexpected backing directory')
    mounts={m['Destination']:m['Source'] for m in ci['Mounts']};require(mounts['/task_ssd']==str(root),'exact backing mount changed')
    key=hashlib.sha256(rid.encode()).hexdigest();path=root/(key+'.bin')
    result=dict(extent_id=rid,path=str(path),expected_nbytes=16 << 20,expected_sha256=expected,container_id=ci['Id'],container_started_at=ci['State']['StartedAt'],boot_id=setup_worker['boot_id'],pids_current=1,bootstrap_ref=ref(a.actual/'measure/bootstrap2.json'),delete_requested=a.delete,exists=path.exists(),other_files_preserved=True)
    if path.exists():
        info=path.lstat();require(stat.S_ISREG(info.st_mode) and info.st_size==16 << 20,'exact extent type/size mismatch')
        actual=hashlib.sha256(path.read_bytes()).hexdigest();require(actual==expected,'exact extent SHA mismatch')
        result.update(actual_nbytes=info.st_size,actual_sha256=actual)
        if a.delete:path.unlink();result['deleted']=True
    else:result['deleted']=False
    result['exists_after']=path.exists();write(a.out/'summary.json',result);print(json.dumps(result))
if __name__=='__main__':main()
