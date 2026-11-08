"""Local-only evidence report; independent of the eight immutable execution assets."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[6]
RESULTS=ROOT/'codex_coding/results/v0.1.23.3/task2/first_op_validation'
N=8 << 20

def read(path): return json.loads(Path(path).read_text())
def ref(path):
    path=Path(path).resolve()
    return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
def require(value,message):
    if not value: raise ValueError(message)
def write(path,value): Path(path).write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--actual',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    actual=a.actual.resolve()
    out=a.out.resolve()
    require(out.parent==RESULTS and out.name.startswith('execution_'),'new local execution report scope required')
    out.mkdir(parents=True,exist_ok=False)
    refs=[]
    def evidence(relative):
        path=actual/relative
        refs.append(ref(path))
        return read(path)
    checks=[]
    def check(value,message):
        require(value,message)
        checks.append(message)
    try:
        summary=evidence('summary.json')
        check(summary['status']=='completed_pending_independent_review','session completed')
        check(summary['GPU_launches']==6 and len(summary['workers'])==9,'six workers nine supervisors')
        check(all(row['returncode']==0 and not row['timed_out'] for row in summary['workers'].values()),'nine supervisor command rc0')
        check(len(summary['all_stop'])==9 and all(row['returncode']==0 for row in summary['all_stop'].values()),'nine own STOP markers delivered')
        check(all(row['passed'] for row in summary['collection'].values()),'three node artifact collection passed')
        setup=evidence('runtime_setup.json')
        check(not setup.get('CPU_REPLAY_ONLY'),'actual GPU runtime, not CPU replay')
        doc=evidence('measure/stage_measurements.json')
        check(doc['schema_version']=='first_op_validation.v1' and doc['fixed_bytes']==N,'fixed canonical schema and 8MiB')
        rows=doc['requests']
        stage=doc['stage']
        order=[1,4,3,2] if stage=='A' else [2,3,4,1]
        check(len(rows)==8 and [r['cell_id'] for r in rows]==[c for c in order for _ in range(2)],'eight fixed stage-order requests')
        check(doc['qualification']['passed'],'native measurement qualification passed')
        journal=evidence('measure/journal.json')
        check(journal['status']=='complete' and journal['GPU_measurements']==8 and not journal.get('failure') and not journal.get('cleanup_failure'),'eight requests and no measurement/cleanup failure')
        bootstrap=evidence('measure/bootstrap0.json')
        check(bootstrap['verification']['passed'] and bootstrap['verification']['checked_bytes']==16 << 20,'source bootstrap full16MiB SHA passed')
        lineage=evidence('measure/source_lineage.json')
        check(lineage['full16MiB_SHA_equal'] and not lineage['model_used'],'source content lineage equal to frozen baseline')
        source_epochs=set()
        executors=set()
        prepares=set()
        metrics=[]
        for r in rows:
            label=f'cell{r["cell_id"]}.R{r["request_ordinal"]}'
            flow=r['flow']
            check(flow['verification']['passed'] and flow['verification']['checked_bytes']==N,label+' all logical target bytes SHA passed')
            hashes=list(flow['verification']['operations'].values())
            check(len(hashes)==1 and hashes[0]['source_sha256']==hashes[0]['target_sha256'],label+' source/target SHA equal')
            check(r['bytes']==N and len(r['ops'])==2 and flow['execution'][4]['records'][0]['actual_copy_bytes']==2*N,label+' two physical8MiB copies')
            check(sum(r['timing_components'].values())==r['worker_execute_ns'],label+' worker timing closes')
            check(sum(r['controller_components'].values())+r['controller_execute_ns']==r['full_ns'],label+' full timing closes')
            check(r['record_total_ns']==sum(op['record_total_ns'] for op in r['ops']),label+' record total closes')
            check(r['diagnosis']['CUDA_events']==0 and r['diagnosis']['metadata_after_both_rounds'] and r['diagnosis']['no_metadata_between_rounds'],label+' no events and metadata only after both copies')
            check(r['diagnosis']['current_request_pre_copy_gpu_operations']==['set_device'],label+' pre-copy GPU call explicitly set_device')
            check(r['executor_birth']!=r['prepare_birth'],label+' independent prepare/executor births')
            check(r['prepare']['intervention']['bytes']==N and r['prepare']['intervention']['kind']==('scratch' if r['target_state']=='fresh' else 'pretouch'),label+' matched prepare intervention')
            check(r['target_writes_before_measurement']==(0 if r['target_state']=='fresh' else 1),label+' exact target write history')
            check(r['prepare']['target_lease_count']==1 and r['fixture']['simultaneous_alive'],label+' genuine fixture lease and simultaneous hold')
            for op in r['ops']:
                check(sum(v['wall_ns'] for v in op['phases'].values())==op['record_total_ns'] and op['host_residual_ns']==0,label+f'.op{op["ordinal"]} phase wall telescope')
                check(sum(v['cpu_ns'] for v in op['phases'].values())==op['record_cpu_ns'],label+f'.op{op["ordinal"]} CPU telescope')
            source_epochs.add(r['diagnosis']['source_identity']['epoch'])
            executors.add(r['executor_birth'])
            prepares.add(r['prepare_birth'])
            op1,op2=r['ops']
            metrics.append(dict(label=label,executor_mode=r['executor_mode'],target_state=r['target_state'],
                first_op_us=op1['record_total_ns']/1000,second_op_us=op2['record_total_ns']/1000,
                source_slice_CPU_us=[op['phases']['source_slice']['cpu_ns']/1000 for op in r['ops']],
                worker_execute_ms=r['worker_execute_ns']/1e6,controller_execute_ms=r['controller_execute_ns']/1e6,
                full_ms=r['full_ns']/1e6,published_ms=r['published_ns']/1e6,
                set_device_us=r['timing_components']['set_device_ns']/1000,
                dispatch_create_join_us=r['timing_components']['dispatch_create_join_ns']/1000,
                executor_birth=r['executor_birth'],prepare_birth=r['prepare_birth'],
                thread_op_ordinals=[op['thread_op_ordinal'] for op in r['ops']]))
        check(len(source_epochs)==1,'one stable source incarnation per stage')
        check(len(executors)==6 and len(prepares)==4,'six executor and four independent prepare generations')
        for cell in order:
            one,two=[r for r in rows if r['cell_id']==cell]
            persistent=one['executor_mode']=='P'
            check((one['executor_birth']==two['executor_birth'])==persistent,f'cell{cell} F/P actual generation policy')
            check(one['prepare_birth']==two['prepare_birth'],f'cell{cell} same prepare context across requests')
            check(two['executor_alive_between_requests']==persistent,f'cell{cell} actual P live/F fresh evidence')
            check(one['executor']['joined']!=persistent and two['executor']['joined'] and not two['executor']['alive_after_join'],f'cell{cell} actual joins')
            check([op['thread_op_ordinal'] for op in one['ops']]==[1,2] and [op['thread_op_ordinal'] for op in two['ops']]==([3,4] if persistent else [1,2]),f'cell{cell} op counters')
            check(one['ops'][0]['target_view']['data_ptr']!=two['ops'][0]['target_view']['data_ptr'],f'cell{cell} simultaneous A/B different addresses')
            fixture=one['fixture']['ranges']
            check(all(fixture[a][1]<=fixture[b][0] or fixture[b][1]<=fixture[a][0] for a,b in [('A','B'),('A','scratch'),('B','scratch')]),f'cell{cell} all fixture address ranges disjoint')
            closed=evidence(f'measure/cell{cell}.close.json')
            check(closed['all_released'] and all(j['joined'] and not j['alive_after_join'] for j in closed['joins']),f'cell{cell} fixture/prepare cleanup')
            check(closed['writes']==({'A':2,'B':2,'scratch':2} if one['target_state']=='fresh' else {'A':3,'B':3,'scratch':0}),f'cell{cell} complete write ledger')
        final=evidence('measure/final_inventory.json')
        check(not final['replicas'] and all(not w['transactions'] and not any(w['used_bytes'].values()) for w in final['workers']),'final VALID/transactions/used bytes all empty')
        check(all(s['files']==0 for s in final['ssd'].values()),'three SSD stores files zero')
        budgets={node:dict(dram_max_observed_bytes=0,hbm_max_observed_bytes=0,dram_limit_bytes=6 << 30,hbm_limit_bytes=2 << 30,
            dram_max_provenance=None,hbm_max_provenance=None,complete_peak_measured=False) for node in ('node28','node30','node27')}
        def add_budget(nodes,provenance):
            for node,data in nodes.items():
                for key,source in [('dram','dram_current_bytes'),('hbm','hbm_absolute_upper_bytes')]:
                    value=data[source]
                    if value>budgets[node][key+'_max_observed_bytes']:
                        budgets[node][key+'_max_observed_bytes']=value
                        budgets[node][key+'_max_provenance']=provenance
        add_budget(evidence('budget_evidence.json')['nodes'],'postwarm gate')
        for sample in evidence('budget_monitor.json'):
            add_budget(sample['nodes'],'active monitor')
        for row in rows:
            add_budget(row['gate'],f'cell{row["cell_id"]}.R{row["request_ordinal"]} fresh gate')
        child=[]
        protected=dict(owners=0,containers=0)
        for node in budgets:
            before=evidence('baseline_'+node+'/stdout.json')
            after=evidence('after_stop_'+node+'/stdout.json')
            check(before['original_xml_sha256']==after['original_xml_sha256'],node+' protected XML SHA unchanged')
            def owners(d):return {r['pid']:{k:r[k] for k in ('pid','start_ticks','cgroup','argv_sha256')} for r in d['protected_owners']}
            def containers(d):return {r['id']:{k:r[k] for k in ('id','name','StartedAt','Running','Pid')} for r in d['protected_containers']}
            check(owners(before)==owners(after),node+' protected owner identities unchanged')
            check(containers(before)==containers(after),node+' protected container identities unchanged')
            check(after['cgroup']['files']['pids.current']=='1',node+' own container quiet pids1')
            protected['owners']+=len(before['protected_owners'])
            protected['containers']+=len(before['protected_containers'])
            collect=evidence('exact_collect_'+node+'/stdout.json')
            check(collect['passed'] and not collect['signals'] and all(r['passed'] and not r['current']['live'] and not r['group'] for r in collect['reports']),node+' exact child process groups empty without further signals')
            for path in (actual/('artifacts_'+node)/'raw').glob('operations_process_*/supervisor.summary.json'):
                refs.append(ref(path))
                sup=read(path)
                check(sup['passed'] and sup['stop_reason']=='owned_stop_marker' and sup['child_returncode']==-15 and
                    sup['cleanup']['passed'] and not sup['cleanup']['pending_group_members'],sup['key']+' supervisor planned TERM/group cleanup')
                dram=max(int(s['memory.current']) for s in sup['samples'] if str(s.get('memory.current','')).isdigit())
                if dram>budgets[node]['dram_max_observed_bytes']:
                    budgets[node]['dram_max_observed_bytes']=dram
                    budgets[node]['dram_max_provenance']='supervisor '+sup['key']+' cgroup sample'
                child.append(dict(key=sup['key'],node=node,child_returncode=sup['child_returncode'],stop_reason=sup['stop_reason'],cleanup_passed=True))
        check(len(child)==9,'nine collected planned TERM child summaries')
        for node,values in budgets.items():
            check(values['dram_max_observed_bytes']<=6 << 30,node+' all observed DRAM below6GiB')
            check(values['hbm_max_observed_bytes']<=2 << 30,node+' all observed same-HIP absolute HBM upper below2GiB')
        result=dict(status='actual_stage_execution_evidence_passed',stage=stage,session_id=doc['session_id'],requests=8,
            measurement_copy_bytes=128 << 20,intervention_copy_bytes=64 << 20,bootstrap_copy_bytes=16 << 20,
            logical_measurement_target_SHA_bytes=64 << 20,bootstrap_target_SHA_bytes=16 << 20,
            fixture_payload_bytes_including_source=40 << 20,metrics=metrics,budget=budgets,protected=protected,children=child,
            observer={k:v for k,v in doc['observer_check'].items() if 'p95' in k},checks=checks,check_count=len(checks),
            GPU_launches_by_this_report=0,remote_writes_by_this_report=0,source_files_modified_by_this_report=0,
            independent_model_gate='delegated to frozen analysis; report makes no B admission or model decision',continuous_peak_measured=False)
        write(out/'summary.json',result)
        write(out/'evidence_refs.json',refs)
        write(out/'qualification.json',dict(passed=True,check_count=len(checks),GPU_launches=0,remote_writes=0,
            summary_ref=ref(out/'summary.json'),evidence_refs_ref=ref(out/'evidence_refs.json')))
        lines=['# 首操作状态阶段'+stage+'执行验收','',f'实际 session `{doc["session_id"]}` 完成八请求；{len(checks)} 项执行证据检查通过。九 supervisor rc0，九 child 按 own STOP→TERM 退出且进程组为空。','',
            '|cell/request|executor/target|op1 µs|op2 µs|source_slice CPU1/2 µs|worker ms|full ms|','|---|---|---:|---:|---|---:|---:|']
        for row in metrics:
            lines.append(f'|{row["label"]}|{row["executor_mode"]}/{row["target_state"]}|{row["first_op_us"]:.3f}|{row["second_op_us"]:.3f}|{row["source_slice_CPU_us"][0]:.3f}/{row["source_slice_CPU_us"][1]:.3f}|{row["worker_execute_ms"]:.6f}|{row["full_ms"]:.6f}|')
        lines+=['','F 的 R2 executor 为新 birth，P 在 R1/R2 保持同一存活 birth，累计 op 序号从1/2推进为3/4；四 cell 的准备线程各独立于 executor，并跨两请求保持同 birth。A/B/scratch 地址范围不重叠且同时持有 lease，fresh 写 scratch、pretouched 写目标；状态全程遵循原产品生命周期。',
            '', '每阶段128MiB测量复制、64MiB准备干预复制，bootstrap16MiB另计；八目标各8MiB全SHA，共64MiB测量目标逻辑SHA，另bootstrap16MiB。两次copy共用目标，仅末次全SHA，不称每轮独立SHA。','',
            '|node|DRAM观测最大 B|HBM同HIP绝对上界观测最大 B|','|---|---:|---:|']
        for node,b in budgets.items():
            lines.append(f'|{node}|{b["dram_max_observed_bytes"]}|{b["hbm_max_observed_bytes"]}|')
        lines+=['','预算合并 postwarm gate、active monitor、八请求 fresh gate 与 supervisor cgroup 样本；所有观测低于每node6GiB DRAM/2GiB HBM。完整连续 peak 未测，具体最大值来源见 summary。',
            '',f'旧保护资产保持：{protected["owners"]} 个 owner、{protected["containers"]} 个 container；原XML SHA不变。最终 VALID/used/transaction 全空、三 SSD files0。',
            '', 'worker/op wall与threadCPU分账及controller/full窗逐请求精确闭合。线程create/queue/join、prepare干预、详细诊断均按实际请求留窗；报告只比较本机duration，没有跨机器timestamp相减。',
            '', '首/后copy差额及因素判别交给事前冻结模型分析；这里不决定B准入，不调整系数，不追加GPU测量。source_slice、logger、copy/sync的首差异不自动指认底层TLS函数或裸带宽。']
        (out/'report.md').write_text('\n'.join(lines)+'\n')
        print(str(out/'qualification.json'))
    except BaseException as exc:
        write(out/'failure.json',dict(status='failed',reason=repr(exc),checks_passed=checks,GPU_launches=0,remote_writes=0))
        raise
if __name__=='__main__':
    main()
