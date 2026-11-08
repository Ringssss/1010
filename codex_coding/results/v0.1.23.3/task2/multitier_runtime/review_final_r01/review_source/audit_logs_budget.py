"""Bind RCCL pair logs to current owners and retain budget accounting limits."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[6]
CAP=2<<30


def main():
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False)
    refs,checks=[],[]
    def freeze(path):
        path=path.resolve();raw=path.read_bytes();dest=a.out/'snapshot'/path.relative_to(ROOT)
        dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
        refs.append(dict(source_path=str(path),path=str(dest),sha256=hashlib.sha256(raw).hexdigest()))
        return raw
    def load(path):return json.loads(freeze(path))
    def check(name,ok):checks.append(dict(name=name,passed=bool(ok)))
    setup=load(a.run_dir/'runtime_setup.json');mesh=load(a.run_dir/'mesh/mesh.json')
    postwarm=load(a.run_dir/'postwarm.inventory.json');qual=load(a.run_dir/'qualification/qualification.json')
    final=load(a.run_dir/'cases/final_inventory.json');journal=load(a.run_dir/'cases/journal.json')
    qualification_journal=load(a.run_dir/'qualification/journal.json')
    wm={w['worker']:w for w in mesh['workers']};comms=defaultdict(list);max_dram={}
    log_rows=[]
    for rank in range(6):
        worker=wm[rank];key='w'+str(rank);directory=a.run_dir/('artifacts_'+worker['node'])/'raw'/('operations_process_'+setup['session']+'_'+key)
        text=freeze(directory/'child.log').decode(errors='replace');owner=load(directory/'child.owner.json')
        supervisor=load(directory/'supervisor.summary.json')
        matches=[]
        for lineno,line in enumerate(text.splitlines(),1):
            match=re.search(r':([0-9]+):[0-9]+ \[[0-9]+\] NCCL INFO ncclCommInitRank comm (0x[0-9a-f]+) rank ([0-9]+) nranks ([0-9]+) cudaDev ([0-9]+) nvmlDev ([0-9]+) busId ([0-9a-f]+) commId (0x[0-9a-f]+) - Init START',line)
            if not match:continue
            pid,ptr,subrank,nranks,gpu,nvml,bus,cid=match.groups()
            row=dict(worker=rank,pid=int(pid),subgroup_rank=int(subrank),nranks=int(nranks),gpu=int(gpu),busId=bus,
                     communicator_pointer=ptr,communicator_id=cid,line=lineno)
            comms[cid].append(row);matches.append(row)
        bdf=worker['runtime_identity']['actual_bdf'];domain,bus,devfunc=bdf.split(':');dev,func=devfunc.split('.')
        numeric=(int(domain,16)<<20)|(int(bus,16)<<12)|(int(dev,16)<<4)|int(func,16)
        check(key+'_actual_PID_GPU_BDF',bool(matches) and owner['pid']==worker['runtime_identity']['pid'] and
              int(owner['start_time'])==worker['runtime_identity']['start_ticks'] and
              all(r['pid']==owner['pid'] and r['gpu']==worker['gpu'] and int(r['busId'],16)==numeric and r['nranks']==2 for r in matches))
        check(key+'_IBext_shca0_gdr0',all(token in text for token in ('Using network IBext_v8','shca_0:1/IB','GPU Direct RDMA Disabled')) and 'NCCL WARN' not in text)
        max_dram[worker['node']]=max(max_dram.get(worker['node'],0),max(int(s['memory.current']) for s in supervisor['samples'] if str(s.get('memory.current','')).isdigit()))
        log_rows.append(dict(worker=rank,start_records=matches,INIT_COMPLETE_line_count=text.count('Init COMPLETE'),
                             log_tail_completeness='stdio may be truncated at coordinated TERM; qualification SHA is the completion evidence'))
    actual_pairs={tuple(sorted(r['worker'] for r in rows)) for rows in comms.values()}
    expected_pairs={tuple(pair) for pair in mesh['necessary_pairs']}
    check('six_needed_pair_communicators',actual_pairs==expected_pairs and len(comms)==6 and
          all(len(rows)==2 and {r['subgroup_rank'] for r in rows}=={0,1} for rows in comms.values()))
    synth_witnesses=qualification_journal['synth']+journal['synth']
    check('all_six_NUMA_touch_register_witnesses',{r['row']['worker'] for r in synth_witnesses}==set(range(6)))
    for row in synth_witnesses:
        info=row['diagnostics']['host_allocation'];w=wm[row['row']['worker']]
        check('NUMA_'+row['row']['id'],info['registered'] is True and info['numa']==w['numa'] and
              info['sampled_page_distribution']=={str(w['numa']):1.0} and info['placement_samples']==256)
    hbm_samples=[]
    for name,workers in [('postwarm',postwarm),('postqualification',qual['postqualification_workers']),('end_of_cases',final['workers'])]:
        for node in ('node28','node30','node27'):
            used=sum(w['hbm_total_bytes']-w['headroom_bytes']['hbm'] for w in workers if w['node']==node)
            hbm_samples.append(dict(stage=name,node=node,absolute_selected_GPU_HIP_used_bytes=used,
                                   cap_bytes=CAP,cap_headroom_bytes=CAP-used,absolute_256MiB_margin_met=used+(256<<20)<=CAP))
            check(name+'_'+node+'_absolute_HIP_upper_below_cap',used<=CAP)
    check('sampled_shared_cgroup_DRAM_plus_margin',all(value+(256<<20)<=6<<30 for value in max_dram.values()))
    result=dict(status='passed' if all(x['passed'] for x in checks) else 'failed',checks=checks,communicators=dict(comms),
        log_rows=log_rows,hbm_samples=hbm_samples,sampled_container_DRAM_max_bytes=max_dram,source_refs=refs,
        budget_boundary='parent increment subtracts sysfs preused from HIP used; counters differ, so this is an estimate. Independent same-HIP absolute occupancy is a conservative cap bound at the three saved points.',
        margin_boundary='node28 absolute HIP cap headroom is 246MiB, 10MiB below 256MiB. The original increment gate passed its 256MiB margin; do not certify that absolute HIP occupancy also retained that margin.',
        physical_peak_measured=False,shared_capacity_measured=False,model_ready=False)
    (a.out/'qualification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(status=result['status'],pair_count=len(comms),checks=len(checks),hbm_samples=hbm_samples[:3],sampled_container_DRAM_max_bytes=max_dram)))
    if result['status']!='passed':raise SystemExit(1)


if __name__=='__main__':main()
