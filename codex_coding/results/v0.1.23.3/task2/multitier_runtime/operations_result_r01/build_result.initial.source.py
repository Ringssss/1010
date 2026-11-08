"""Independently summarize existing local operational artifacts; no remote calls."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path('/home/zhujianian/zhujianian/fluidckpt_zjn')
OUT = Path(__file__).resolve().parent
ACT = OUT.parent/'operations_actual_r01'
PLAN = OUT.parent/'operations_launch_r01/launch_plan.json'
HELPER = ROOT/'codex_coding/src/v0.1.23.3/task2/three_node_recovery/admission28/runtime_gate_r03.py'


def load(path):
    return json.loads(path.read_text())


def ref(path):
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def write(path,value):
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def protected(rows):
    result = {}
    for row in rows:
        item = {k:row[k] for k in ('id','name','StartedAt','Running','Pid')}
        if row['identity']:
            stat = row['identity']['stat']
            item.update(start=stat.rsplit(') ',1)[1].split()[19],cgroup=row['identity']['cgroup'])
        result[row['id']] = item
    return result


def owners(rows):
    return {r['pid']:{k:r[k] for k in ('start_ticks','cgroup','argv_sha256')} for r in rows}


def main():
    ms = importlib.util.spec_from_file_location('verified_dump_view',HELPER)
    gate = importlib.util.module_from_spec(ms)
    ms.loader.exec_module(gate)
    plan,summary = load(PLAN),load(ACT/'summary.json')
    budget,postwarm,monitor = load(ACT/'budget_evidence.json'),load(ACT/'postwarm.inventory.json'),load(ACT/'budget_monitor.json')
    manifest = {'read_only_local_analysis':True,'GPU_calls':0,'SSH_calls':0,'signals':[],
                'source_refs':[ref(Path(__file__)),ref(HELPER)],'input_refs':[],'nodes':{},'processes':{},'transport':{},'checks':{}}
    used = {PLAN,ACT/'summary.json',ACT/'budget_evidence.json',ACT/'postwarm.inventory.json',ACT/'budget_monitor.json'}
    source_manifest = load(OUT.parent/'operations_source_r02/source_manifest.json')
    expected_sources = {r['relative_path']:r['sha256'] for r in source_manifest['assets']}
    used.add(OUT.parent/'operations_source_r02/source_manifest.json')
    manifest['checks']['all_nine_supervisors_rc0'] = len(summary['workers'])==9 and all(p['returncode']==0 and not p['timed_out'] for p in summary['workers'].values())
    manifest['checks']['all_nine_STOP_rc0'] = len(summary['all_stop'])==9 and all(p['returncode']==0 for p in summary['all_stop'].values())
    for node in ('node28','node30','node27'):
        before_path,after_path = ACT/('baseline_'+node)/'stdout.json',ACT/('after_stop_'+node)/'stdout.json'
        before,after = load(before_path),load(after_path)
        used.update((before_path,after_path))
        exact_path = ACT/('exact_collect_'+node)/'stdout.json'
        exact = load(exact_path)
        used.add(exact_path)
        checks = {'same_boot':before['boot_id']==after['boot_id'],
            'same_task_container':before['container']['Id']==after['container']['Id'] and before['container']['State']['StartedAt']==after['container']['State']['StartedAt'],
            'old_containers_unchanged':protected(before['protected_containers'])==protected(after['protected_containers']),
            'old_owners_unchanged':owners(before['protected_owners'])==owners(after['protected_owners']),
            'original_XML_mapping_unchanged':before['original_xml_sha256']==after['original_xml_sha256'],
            'source_stage_unchanged':before['source_assets']==after['source_assets'],
            'final_source_manifest_matches':all(after['source_assets'].get(k)==v for k,v in expected_sources.items()),
            'final_pids1':after['cgroup']['files']['pids.current']=='1',
            'no_OOM':{'oom 0','oom_kill 0','max 0'}.issubset(set(after['cgroup']['files']['memory.events'].splitlines())),
            'exact_children_absent_group_empty':exact['passed'] and all(not r['current']['live'] and not r['group'] for r in exact['reports'])}
        rows = [r for r in plan['processes'] if r['node']==node]
        samples = []
        for row in rows:
            p = ACT/('artifacts_'+node)/'raw'/Path(row['out']).name/'supervisor.summary.json'
            s = load(p)
            used.add(p)
            samples.extend(s['samples'])
            manifest['processes'][row['key']] = {'node':node,'child_returncode':s['child_returncode'],'stop_reason':s['stop_reason'],
                'supervisor_passed':s['passed'],'cleanup_passed':s['cleanup']['passed'],'cleanup_signals':s['cleanup']['signals'],
                'owner_pid':s['cleanup']['before']['pid'],'sample_count':len(s['samples']),'owner_ref':ref(p.with_name('child.owner.json'))}
            checks[row['key']+'_planned_TERM_only'] = s['passed'] and s['stop_reason']=='owned_stop_marker' and s['child_returncode']==-15 and s['cleanup']['signals']==['TERM']
        dram_max = max(int(s['memory.current']) for s in samples if isinstance(s.get('memory.current'),str) and s['memory.current'].isdigit())
        group = [w for w in postwarm if w['node']==node]
        absolute = sum(w['hbm_total_bytes']-w['headroom_bytes']['hbm'] for w in group)
        mon = [s for s in monitor if s['node']==node]
        delta = max(s['hbm_increment_upper'] for s in mon)-budget['nodes'][node]['hbm_increment_bytes']
        absolute_peak = absolute+delta
        checks['sampled_DRAM_under_6GiB_plus_margin'] = dram_max+(256<<20)<=6<<30
        checks['same_HIP_sampled_absolute_HBM_under_2GiB'] = absolute_peak<=2<<30
        manifest['nodes'][node] = {'checks':checks,'passed':all(checks.values()),'cgroup_observed_max_bytes':dram_max,
            'cgroup_DRAM_margin_bytes':(6<<30)-dram_max,'cgroup_final_bytes':int(after['cgroup']['files']['memory.current']),
            'cgroup_kernel_peak_available':all(isinstance(s.get('memory.peak'),str) for s in samples),
            'postwarm_HIP_total_minus_free_absolute_bytes':absolute,'HIP_same_counter_sampled_absolute_upper_bytes':absolute_peak,
            'sampled_absolute_HBM_margin_bytes':(2<<30)-absolute_peak,'absolute_upper_supports_256MiB_margin':absolute_peak+(256<<20)<=2<<30,
            'task_HBM_increment_estimate_postwarm_bytes':budget['nodes'][node]['hbm_increment_bytes'],
            'task_HBM_increment_estimate_monitor_max_bytes':max(s['hbm_increment_upper'] for s in mon),
            'HIP_free_monitor_samples':len(mon),'supervisor_shared_cgroup_samples':len(samples),
            'HBM_estimate_scope':'28/30 baseline sysfs used is not the HIP total-free counter; estimate only; node27 zero baseline is conservative absolute occupancy',
            'complete_physical_peak_certified':False,'original_xml_sha256':after['original_xml_sha256'],
            'protected_container_count':len(after['protected_containers']),'protected_owner_count':len(after['protected_owners'])}
    for row in plan['processes']:
        if row['kind']!='worker':
            continue
        key,node = row['key'],row['node']
        artifact = ACT/('artifacts_'+node)/'raw'
        rawpath = artifact/('operations_dump_'+plan['session'])/(key+'.runtime.dump.xml')
        logpath = artifact/Path(row['out']).name/'child.log'
        ownerpath = logpath.with_name('child.owner.json')
        used.update((rawpath,logpath,ownerpath))
        view,view_report = gate.dump_parse_view(rawpath.read_bytes())
        viewpath = OUT/(key+'.parse_view.xml')
        viewpath.write_bytes(view)
        write(OUT/(key+'.parse_view.json'),view_report)
        ownpid = load(ownerpath)['pid']
        lines = [l for l in logpath.read_text().splitlines() if re.search(rf':{ownpid}:\d+\s+\[{row["gpu"]}\]',l)]
        identities = []
        for line in lines:
            match = re.search(r'ncclCommInitRank comm (\S+) rank (\d+) nranks (\d+) cudaDev (\d+) nvmlDev (\d+) busId ([0-9a-fA-F]+).* - Init (START|COMPLETE)',line)
            if match:
                comm,rank,world,cuda,nvml,bus,phase = match.groups()
                identities.append({'comm':comm,'rank':int(rank),'world_size':int(world),'cuda_device':int(cuda),'nvml_device':int(nvml),'bus_id':int(bus,16),'phase':phase,'line':line})
        want_rank = int(row['env']['NCCL_TOPO_DUMP_FILE_RANK'])
        candidates = [x for x in identities if x['rank']==want_rank and x['world_size']==2 and x['cuda_device']==row['gpu'] and x['nvml_device']==row['gpu'] and x['bus_id']==gate.bdf_bus_id(row['bdf']) and x['phase']=='COMPLETE']
        communication = {'passed':bool(candidates),'device_binding':dict(candidates[0],passed=True) if candidates else {},
                         'actual_net_protocols':sorted(set(re.findall(r'via NET/([^/\s]+)/','\n'.join(lines)))),
                         'selected_HCA_ports':sorted(set(re.findall(r'\[\d+\]([A-Za-z0-9_]+):(\d+)/IB\b','\n'.join(lines))))}
        inputpath = Path(row['env']['NCCL_TOPO_FILE'])
        used.add(inputpath)
        gp = gate.dump_check(inputpath.read_bytes(),view,row['bdf'],want_rank,row['gpu'],communication)
        net = gate.net_dump_check(inputpath.read_bytes(),view,communication)
        loaded = [l for l in lines if 'Loading topology file '+str(inputpath) in l]
        fallback = [l for l in lines if 'Loading topology mapping file' in l]
        transport = {'GPU_actual_dump_check':gp,'HCA_actual_dump_check':net,'input_loading_lines':loaded,
            'fallback_mapping_lines':fallback,'network_lines':[l for l in lines if 'Using network ' in l or 'NET/IB : Using ' in l],
            'channels':[l for l in lines if 'via NET/' in l or 'via P2P/' in l or 'via SHM/' in l],
            'communicator_identities':identities,'raw_dump_ref':ref(rawpath),'derived_view_ref':ref(viewpath),
            'input_ref':ref(inputpath),'log_ref':ref(logpath),'passed':gp['passed'] and net['passed'] and bool(loaded) and not fallback,
            'scope':'one per-worker observed necessary pair rank view plus own log channels; runtime dump can be overwritten by later pair, not certification of all 15 possible pairs'}
        write(OUT/(key+'.transport.json'),transport)
        manifest['transport'][key] = {'passed':transport['passed'],'input_ref':ref(inputpath),'raw_dump_ref':ref(rawpath),
            'derived_view_ref':ref(viewpath),'GPU_BDF':row['bdf'],'NUMA':row['numa'],'HCA':'shca_0:1','network':'IBext_v8',
            'empty_attribute_tokens_removed':view_report['removed_token_count']}
    cases = load(ACT/'cases/summary.json')
    manifest['case_status'] = {r['phase']:{'verified':r['restore']['verification']['passed'],'bytes':r['restore']['verification']['checked_bytes']} for r in cases['results']}
    manifest['checks']['all_four_cases_full_SHA32MiB'] = len(manifest['case_status'])==4 and all(r['verified'] and r['bytes']==32<<20 for r in manifest['case_status'].values())
    manifest['checks']['all_six_GPU_NET_xml_logs_verified'] = all(r['passed'] for r in manifest['transport'].values())
    manifest['checks']['all_three_nodes_operations_passed'] = all(r['passed'] for r in manifest['nodes'].values())
    manifest['checks']['all_current_source_refs_match_local'] = all(hashlib.sha256(Path(path).read_bytes()).hexdigest()==sha for path,sha in load(ACT/'cases/source_shas.json').items())
    used.update((ACT/'cases/summary.json',ACT/'cases/source_shas.json',ACT/'mesh/source_shas.json',ACT/'qualification/source_shas.json'))
    manifest['input_refs'] = [ref(p) for p in sorted(used)]
    manifest['passed'] = all(manifest['checks'].values())
    manifest['elapsed_s'] = summary['elapsed_s']
    manifest['complete_physical_peak_certified'] = False
    write(OUT/'operations_result.json',manifest)
    lines = ['# 三节点六 worker 实际运行：部署与监督复核','',
        f"本轮只读复核实际 session `{plan['session']}`。父运行控制链 {summary['elapsed_s']:.4f} 秒完成；9 个监督器返回0，三节点收集通过。SSD_only、C0、C1、C3各恢复32MiB完整SHA通过。",'',
        '| 节点 | DRAM观察最大 GiB | DRAM余量 MiB | 同HIP口径HBM观察上界 GiB | HBM绝对上界余量 MiB | 终态cgroup MiB |',
        '|---|---:|---:|---:|---:|---:|']
    for node,n in manifest['nodes'].items():
        lines.append(f"| {node} | {n['cgroup_observed_max_bytes']/2**30:.4f} | {n['cgroup_DRAM_margin_bytes']/2**20:.1f} | {n['HIP_same_counter_sampled_absolute_upper_bytes']/2**30:.4f} | {n['sampled_absolute_HBM_margin_bytes']/2**20:.1f} | {n['cgroup_final_bytes']/2**20:.2f} |")
    lines += ['',
        'DRAM按同一个容器cgroup计费，两个worker和SSD监督器的采样合并取最大，不能相加。每个监督器299次、约250ms采样；所有节点观察值加256MiB均低于6GiB。kernel没有memory.peak文件，观察最大是采样下界。', '',
        'HBM重新用HIP total−free口径计算整个两卡绝对占用，postwarm值加同口径运行free下降得到上表观察上界；其中也包含已有占用。三节点均小于2GiB。node28上界余226MiB，因此这个绝对上界不能独立证明256MiB任务增量余量。父原准入在28/30使用sysfs used扣除旧基线，它与HIP total−free不是同一计数口径，只保留为增量估计，原始准入与运行结果不改写。node27基线扣0是保守绝对占用。2秒HBM监测也不能证明完整物理峰值。', '',
        '9个child由匹配STOP触发精确TERM退役，child返回−15；没有KILL、超时或身份变更。不是9个child自然返回0。独立收尾读取确认这些PID不存活且原process group为空，三容器保留sleep进程，pids.current=1，oom/oom_kill/max均0。旧容器、node28的13个保护owner、原XML/default mapping及源码stage哈希保持。host原厂XML此前即不存在，记None；不推断容器namespace中该原厂文件的保护状态。', '',
        '六worker日志均记录加载各自任务input XML；实际通信观察IBext_v8、shca_0:1/IB、ib0，未见默认mapping fallback。对raw dump沿用已验证dump_parse_view，仅在本目录生成派生XML，删除GPU/NET匿名空属性token，原件保持。六份GPU BDF/完整PCI链/NUMA/affinity/gdr0与所选HCA链验证通过；每份是某个必要pair的局部rank dump，不能冒称全部15个可能pair路径均实测。', '',
        '源码SHA与真实stage/mesh/qualify/cases记录一致。本报告是部署、已观察预算和精确清理证据；完整peak、冷磁盘与真实模型T_ready仍未认证。结构化入口：`operations_result.json`；`w0`至`w5.transport.json`及对应parse_view文件提供实际设备路径原件引用。']
    (OUT/'report.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'passed':manifest['passed'],'checks':manifest['checks'],'nodes':{n:r['passed'] for n,r in manifest['nodes'].items()}}))


if __name__=='__main__':
    main()
