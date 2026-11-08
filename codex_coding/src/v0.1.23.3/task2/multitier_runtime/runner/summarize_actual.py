"""Read-only aggregation of completed live native-API results and exact cleanup."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

MIB = 1 << 20
Q = 4 * MIB
PHASES = ('SSD_only', 'C0', 'C1', 'C3')
NODES = ('n0', 'n1', 'n2')
NODE_NAMES = dict(n0='node28', n1='node30', n2='node27')
IO_KEYS = ('logical_http_read_bytes', 'server_kernel_read_bytes', 'written_bytes',
           'server_kernel_write_bytes', 'committed_bytes')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, sort_keys=True) + '\n')


def timing(transfer):
    if transfer is None:
        return dict(operation_present=False, prepare_ms=None, execute_ms=None,
                    filled_ms=None, verify_publish_ms=None, published_ms=None)
    stamps = transfer['controller_timing']
    begin, filled, published = [stamps[k] for k in ('load_begin_ns', 'all_targets_filled_ns', 'all_targets_published_ns')]
    require(begin <= filled <= published, 'same-clock timestamps out of order')
    result = dict(operation_present=True, clock_domain=stamps['clock_domain'],
                  load_begin_ns=begin, all_targets_filled_ns=filled, all_targets_published_ns=published,
                  prepare_ms=transfer['prepare_s'] * 1000, execute_ms=transfer['execute_wall_s'] * 1000,
                  filled_ms=(filled-begin)/1e6, verify_publish_ms=(published-filled)/1e6,
                  published_ms=(published-begin)/1e6, inventory_rpc_ms=transfer['inventory_rpc_s']*1000,
                  prepare_rpc_ms=transfer['prepare_rpc_s']*1000)
    require(abs(result['filled_ms']-result['prepare_ms']-result['execute_ms']) < .01, 'filled decomposition mismatch')
    require(abs(result['published_ms']-transfer['coordinator_wall_s']*1000) < .01, 'published decomposition mismatch')
    return result


def verify(transfer):
    require(transfer['verification']['passed'] is True, 'transfer verification failed')
    operations = transfer['verification']['operations']
    require(len(operations) == len(transfer['plan']['ops']), 'missing full-SHA operations')
    total = 0
    for op in transfer['plan']['ops']:
        row = operations[str(op['op_id'])]
        source, target = row['source_sha256'], row['target_sha256']
        require(len(source) == 64 and source == target and row['n'] == op['n'], 'every-byte SHA mismatch')
        total += row['n']
    require(total == transfer['verification']['checked_bytes'], 'verified byte total mismatch')
    return total, len(operations)


def add_io(total, observed):
    for node in NODES:
        for key in IO_KEYS:
            total[node][key] += observed[node][key]


def fresh_io():
    return {node: {key:0 for key in IO_KEYS} for node in NODES}


def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |', '| '+' | '.join(['---']*len(headers))+' |'] +
                     ['| '+' | '.join(str(v) for v in row)+' |' for row in rows])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root = Path(args.input).resolve()
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    references = {}
    def read(relative):
        path = root / relative
        references[relative] = dict(path=str(path), sha256=digest(path))
        return json.loads(path.read_text())
    overview = read('cases/summary.json')
    base = read('cases/base.json')
    qualification = read('qualification/qualification.json')
    qjournal = read('qualification/journal.json')
    journal = read('cases/journal.json')
    final = read('cases/final_inventory.json')
    binding = read('cases/binding.json')
    source_refs = {stage:read(stage+'/source_shas.json') for stage in ('mesh', 'qualification', 'cases')}
    require(overview['status'] == 'complete' and qualification['status'] == 'complete', 'live run incomplete')
    require(qualification['purpose'] == 'physical' and qualification['mode'] == 'bounded_qualification', 'physical qualification missing')
    require(base['complete_bytes'] == 32*MIB and base['distribution_bytes'] == [16*MIB, 8*MIB, 8*MIB], 'complete SSD base mismatch')
    require(base['fsync_by_native_complete'] is True and base['synthetic'] is True, 'base provenance mismatch')
    require(len(base['current_catalog']) == 8 and all(r['tier']=='ssd' and r['state']=='VALID' and r['leases']==0 and r['n']==Q for r in base['current_catalog']), 'invalid base Catalog')
    blocks = {}
    verified_bytes = 0
    sha_operations = 0
    base_transfer_timing = []
    for transfer in base['transfers']:
        count, ops = verify(transfer)
        verified_bytes += count
        sha_operations += ops
        base_transfer_timing.append(timing(transfer))
        targets = {t['key']:t for t in transfer['plan']['targets']}
        for op in transfer['plan']['ops']:
            bid = targets[op['target']]['id'].rsplit('/',1)[1]
            row = transfer['verification']['operations'][str(op['op_id'])]
            blocks[bid] = dict(nbytes=row['n'], sha256=row['target_sha256'], tp=op['tp'], off=op['off'])
    require(set(blocks) == {f'b{i}' for i in range(8)}, 'base blocks not fully hashed')
    qualification_io = fresh_io()
    qualification_rows = []
    qclocks = set()
    for key, proof in qualification['paths'].items():
        count, ops = verify(proof['transfer'])
        require(count == Q and proof['status'] == 'verified', 'selected path missing 4MiB SHA')
        verified_bytes += count
        sha_operations += ops
        qclocks.add(proof['transfer']['controller_timing']['clock_domain'])
        if proof['source_bootstrap']:
            count, ops = verify(proof['source_bootstrap'])
            verified_bytes += count
            sha_operations += ops
        add_io(qualification_io, proof['ssd_io'])
        qualification_rows.append(dict(path=key, source_worker=proof['source_worker'], target_worker=proof['target_worker'],
            source_tier=proof['source_tier'], target_tier=proof['target_tier'], nbytes=Q, full_sha_passed=True,
            timing_ms=timing(proof['transfer'])))
    require(len(qualification_rows) == 23 and len(qclocks) == 1, 'qualification path/clock mismatch')
    require(qjournal['status'] == 'complete' and not qjournal['unadopted_shards'], 'qualification cleanup incomplete')
    case_clock_domains = set()
    cases = []
    helper_routes = []
    phase_io = dict(qualification=qualification_io, base_build=overview['base_build_ssd_io'])
    for phase in PHASES:
        data = read('cases/'+phase+'.json')
        require(data['phase'] == phase and data['status'] == 'complete', 'case missing/incomplete')
        raw_summary = next(r for r in overview['results'] if r['phase']==phase)
        require(raw_summary['save'] == data['save'] and raw_summary['restore'] == data['restore'] and raw_summary['ssd_io'] == data['ssd_io'], 'case vs summary mismatch')
        require(set(data['cleanup']['base_ids_preserved']) == set(base['base_ids'].values()) and data['cleanup']['preservation_checked_from_current_catalog'], 'base preservation missing')
        require(set(data['cleanup']['retired']) == set(data['owned_ids']), 'optional/target exact cleanup incomplete')
        rows = {r['id']:r for r in data['base_descriptors'] + data['owned_descriptors']}
        retained = {tier:sum(t['n'] for t in data['save_plan']['targets'] if t['tier']==tier) for tier in ('hbm', 'dram')}
        source_bytes = dict(hbm=0, dram=0, ssd=0)
        source_by_worker = {}
        derived_ssd = {stage:{node:dict(logical_transfer_read_bytes=0, verification_source_read_bytes=0,
            logical_http_read_bytes_derived=0, server_kernel_read_bytes=None) for node in NODES} for stage in ('save','restore')}
        for stage in ('save', 'restore'):
            transfer = data[stage]
            if transfer is None:
                require(not data[stage+'_plan']['targets'], 'missing nonempty save transfer')
                continue
            count, ops = verify(transfer)
            verified_bytes += count
            sha_operations += ops
            case_clock_domains.add(transfer['controller_timing']['clock_domain'])
            for op in transfer['plan']['ops']:
                verification = transfer['verification']['operations'][str(op['op_id'])]
                bid = f'b{op["tp"]*2+op["off"]//Q}'
                require(verification['source_sha256'] == blocks[bid]['sha256'], 'case content diverged from authoritative base')
                source = rows[op['replica_id']]
                if source['tier'] == 'ssd':
                    node = next(k for k,v in NODE_NAMES.items() if v==source['storage_node'])
                    entry = derived_ssd[stage][node]
                    entry['logical_transfer_read_bytes'] += op['n']
                    entry['verification_source_read_bytes'] += op['n']
                    entry['logical_http_read_bytes_derived'] += 2*op['n']
                if stage == 'restore':
                    source_bytes[source['tier']] += op['n']
                    source_by_worker.setdefault(str(op['src_w']),dict(hbm=0,dram=0,ssd=0))[source['tier']] += op['n']
                    if op['src_w'] in (1,3):
                        records = next(w['records'] for w in transfer['execution'] if w['worker']==op['src_w'])
                        require(any(r['op_id']==op['op_id'] and r['role']=='source' and r['bytes']==Q for r in records), 'helper source not actually executed')
                        helper_routes.append(dict(phase=phase,block=bid,source_worker=op['src_w'],target_worker=op['dst_w'],
                            source_node=source['node'],source_gpu=source['gpu'],source_numa=source['numa'],nbytes=op['n'],
                            kind=op['kind'],full_sha256=verification['target_sha256']))
            if stage == 'restore':
                require(count == 32*MIB and len(transfer['plan']['targets'])==4, 'restore incomplete')
                for target in transfer['plan']['targets']:
                    operations = sorted([op for op in transfer['plan']['ops'] if op['target']==target['key']],key=lambda op:op['off'])
                    require([(op['off'],op['n']) for op in operations]==[(0,Q),(Q,Q)], 'target byte coverage incomplete')
        for node in NODES:
            require(sum(derived_ssd[st][node]['logical_http_read_bytes_derived'] for st in ('save','restore')) == data['ssd_io'][node]['logical_http_read_bytes'], 'derived vs measured SSD HTTP bytes mismatch')
        require(sum(source_bytes.values())==32*MIB, 'restore source ledger mismatch')
        require(sum(t['n'] for t in data['restore_plan']['targets'])==32*MIB, 'HBM target ledger mismatch')
        result = dict(phase=phase,repetitions=1,save_timing_ms=timing(data['save']),restore_timing_ms=timing(data['restore']),
            retention_bytes=retained,retention_by_worker={str(w):{tier:sum(t['n'] for t in data['save_plan']['targets'] if t['worker']==w and t['tier']==tier) for tier in ('hbm','dram')} for w in range(6)},
            target_hbm_bytes=32*MIB,complete_ssd_base_bytes=32*MIB,serial_staging_hbm_bytes=384*MIB,
            known_hbm_items_bytes=384*MIB+32*MIB+retained['hbm'],complete_physical_peak_bytes=None,
            restore_source_bytes=source_bytes,restore_source_bytes_by_worker=source_by_worker,
            measured_ssd_io_save_and_restore=data['ssd_io'],derived_ssd_io_by_stage=derived_ssd,
            per_stage_kernel_io_measured=False,all_target_bytes_full_sha_passed=True,exact_owned_cleanup_passed=True,
            complete_base_preserved_at_case_cleanup=True)
        cases.append(result)
        phase_io[phase+'_save_and_restore'] = data['ssd_io']
    require(len(case_clock_domains)==1, 'formal timings mixed clock domains')
    require(not final['replicas'] and all(not w['transactions'] for w in final['workers']), 'final Catalog/transactions not empty')
    require(journal['base_preserved_through_every_case'] and journal['base_retirement_allowed'] and not journal['unadopted_shards'], 'final lifetime journal incomplete')
    release_events = [e for e in journal['cleanup'] if e['event']=='explicit_end_of_run_full_base_release']
    require(len(release_events)==1 and {r['id'] for r in release_events[0]['objects']}==set(base['base_ids'].values()) and all(r['status']=='released' for r in release_events[0]['objects']), 'explicit full-base release missing')
    numa = []
    for worker in final['workers']:
        allocations = worker['host_allocations']
        require(allocations and all(a['registered'] and a['backend']=='numa_registered' and
            a['sampled_page_distribution']=={str(a['numa']):1.0} and a['placement_samples']==256 for a in allocations), 'NUMA/register evidence mismatch')
        require(any(a['logical_bytes']==Q and a['numa']==worker['numa'] for a in allocations), 'worker lacks actual 4MiB NUMA qualification')
        numa.append(dict(worker=worker['worker'],node=worker['node'],gpu=worker['gpu'],numa=worker['numa'],
            actual_bdf=worker['runtime_identity']['actual_bdf'],historical_host_allocations=len(allocations),
            four_mib_touched_sampled_registered=True,staging_hbm_bytes=worker['hbm_allocated_bytes']))
    stopped = {}
    for node in NODE_NAMES.values():
        collect = read('exact_collect_'+node+'/stdout.json')
        after = read('after_stop_'+node+'/stdout.json')
        require(collect['passed'] and all(r['passed'] and not r['current']['live'] and not r['group'] for r in collect['reports']), 'owned process/group remains')
        require(after['cgroup']['files']['pids.current']=='1' and all(after['ports_free'].values()), 'post-stop task endpoints remain')
        stopped[node] = dict(owned_processes_absent=True,owned_groups_empty=True,container_pids_current=1,
            retained_idle_container=True,task_ports_free=after['ports_free'],poststop_cgroup_memory_bytes=int(after['cgroup']['files']['memory.current']))
    summary = dict(schema='multitier.live_result_aggregation.v1',development='v0.1.23.3/task2',status='parsed_and_verified',
        session=qualification['session'],synthetic=True,payload_description='synthetic processed TP4-schema bytes; four 8MiB ranks',
        payload_bytes=32*MIB,physical_nodes=NODE_NAMES,necessary_path_count=23,necessary_path_results=qualification_rows,
        qualification_clock_domain=next(iter(qclocks)),formal_clock_domain=next(iter(case_clock_domains)),
        cases=cases,base=dict(payload_bytes=32*MIB,distribution_bytes=[16*MIB,8*MIB,8*MIB],blocks=blocks,
            native_fsync_before_publish=True,transfers_timing_ms=base_transfer_timing,full_sha_passed=True),
        sha_validation=dict(operations=sha_operations,checked_logical_bytes=verified_bytes,each_operation_source_and_target_full_sha=True),
        measured_ssd_io_windows=phase_io,numa_workers=numa,helper_actual_restore_routes=helper_routes,
        cleanup=dict(final_catalog_empty=True,final_transactions_empty=True,complete_base_preserved_through_cases=True,
            explicit_end_of_run_base_release=True,unadopted_shards_empty=True,stopped_nodes=stopped),
        executed_source_shas=source_refs,input_refs=references,parser_source_ref=dict(path=str(Path(__file__).resolve()),sha256=digest(__file__)),
        scope=dict(repetitions_each=1,performance_statistical_claim=False,solver_delta_evaluated=False,model_ready_evaluated=False,
            complete_physical_peak_measured=False,cold_disk_evaluated=False,
            instrumented_functional_timing=True,inventory_diagnostic_cost_separately_measured=False,
            timing='native same-clock CPU controller transfer windows; SHA verification included only in verify+publish',
            io='case counters encompass save and restore; per-stage HTTP reads derived from ops and source verification, per-stage kernel attribution unavailable'))
    write(out/'summary.json',summary)
    lines = ['# 三节点六GPU多层恢复：一次实机结果', '',
        '三台机器六个GPU worker完成23条必要路径资格，并完成SSD_only、C0、C1、C3各一次完整32MiB恢复。八个4MiB SSD底本按node28/node30/node27的16/8/8MiB发布，四个8MiB目标的每个逻辑字节均通过SHA256；C3的两个helper实际跨节点供数。', '',
        f'本报告只读解析session `{qualification["session"]}` 的原件，开发编号 `v0.1.23.3/task2`。载荷是合成processed TP4 schema字节。物理路径、字节可用和生命周期已执行；研究结论范围为每配置n=1的功能观察。完整模型handoff、solver策略差异、统计性能结论和完整物理峰值留待后续独立评估。', '',
        '## 同钟时延', '',
        '单位ms。prepare含inventory与prepare RPC；execute为全部worker执行返回的控制端墙钟窗口；filled/published均从同一monotonic_ns起点计算；verify+publish含全SHA、完成与发布。保存计时从已有完整SSD底本建立选定快层副本；初始合成与NUMA分配在该窗口之外，SSD底本构建保留独立transfer时间戳。', '',
        table(['配置','阶段','inventory RPC','prepare RPC','prepare合计','execute','filled','verify+publish','published'],
            [[c['phase'],stage]+[('—' if t.get(k) is None else f'{t[k]:.3f}') for k in ('inventory_rpc_ms','prepare_rpc_ms','prepare_ms','execute_ms','filled_ms','verify_publish_ms','published_ms')]
             for c in cases for stage,t in [('save',c['save_timing_ms']),('restore',c['restore_timing_ms'])]]), '',
        '恢复inventory RPC约61.8–63.3ms；C0/C1的filled窗口主要包含这部分控制请求。prepare合计包含inventory，不代表GPU分配耗时。当前窗口来自带RuntimeWorker诊断的功能入口；该inventory每次还采集HIP BDF、sysfs NUMA、进程/cgroup和通信诊断，诊断负担是后续可检验的因素，现有数据尚未分离其成本。', '',
        f'正式四配置共用时钟域 `{next(iter(case_clock_domains))}`。SSD_only没有快层保存操作，表中为“—”。资格路径来自另一个独立控制进程，时钟域 `{next(iter(qclocks))}`；其时间戳单独保存，没有与正式控制进程相减。', '',
        '## 暂停空间与实际恢复来源', '',
        table(['配置','暂停HBM MiB','暂停DRAM MiB','恢复HBM来源 MiB','恢复DRAM来源 MiB','恢复SSD来源 MiB','目标HBM MiB','已知HBM项 MiB'],
            [[c['phase'],c['retention_bytes']['hbm']/MIB,c['retention_bytes']['dram']/MIB,c['restore_source_bytes']['hbm']/MIB,
              c['restore_source_bytes']['dram']/MIB,c['restore_source_bytes']['ssd']/MIB,32,c['known_hbm_items_bytes']/MIB] for c in cases]), '',
        '完整SSD底本恒为32MiB，已知HBM项包含六worker的384MiB原serial staging、32MiB目标及暂停HBM副本。runtime、驱动、注册、主机读取buffer、pagecache与过渡分配需进入实际空间账本，表中已知项对应部分分配，完整物理峰值尚未测得。', '',
        '## SSD服务I/O窗口', '',
        '下表均为各SSD服务实测counter差值，单位MiB；kernel read/write来自服务进程/proc/self/io。qualification窗口包含小载荷source bootstrap、已选路径和SHA读入。每配置窗口包围save+restore，含源端全SHA再次读取。', '',
        table(['窗口','节点','HTTP读取','kernel读取','HTTP写入','kernel写入','fsync提交'],
            [[window,NODE_NAMES[node]]+[f'{io[node][key]/MIB:g}' for key in IO_KEYS] for window,io in phase_io.items() for node in NODES]), '',
        'SSD底本初始构建的kernel读取为0，随后四配置服务读取各64MiB，其中逻辑传输32MiB、源端SHA校验再次读取32MiB。buffered读取协议与POSIX_FADV_DONTNEED保持原实现；这些kernel计数刻画服务I/O，不构成可重复的冷盘读时延。', '',
        '分阶段HTTP读取量根据实际plan与原verify_run的SSD源端读取计算，并与实测窗口总量核对；这一拆分为推导值。原件缺少save/restore边界处的kernel counter采样，因此kernel两阶段归属在JSON中保留null。', '',
        table(['配置','阶段','node28传输/SHA MiB','node30传输/SHA MiB','node27传输/SHA MiB'],
            [[c['phase'],stage]+[f"{io[node]['logical_transfer_read_bytes']/MIB:g}/{io[node]['verification_source_read_bytes']/MIB:g}" for node in NODES]
             for c in cases for stage,io in c['derived_ssd_io_by_stage'].items()]), '',
        '## 完整底本、NUMA与helper见证', '',
        table(['底本块','rank','偏移MiB','字节MiB','完整SHA256'],
            [[bid,b['tp'],b['off']/MIB,b['nbytes']/MIB,b['sha256']] for bid,b in sorted(blocks.items())]), '',
        '八SSD extent的原native complete执行文件fsync后发布Catalog。保存与恢复的每个op都与对应权威底本SHA一致；四target均为[0,4MiB)、[4MiB,8MiB)完整覆盖。当前owner贯穿四配置，相关证据认证本次活owner生命周期。', '',
        table(['worker','节点','GPU','实际BDF','NUMA','4MiB触页/256样本/注册','历史host分配条目'],
            [[w['worker'],w['node'],w['gpu'],w['actual_bdf'],w['numa'],'通过',w['historical_host_allocations']] for w in numa]), '',
        table(['配置/块','helper位置','target worker','来源MiB','实际路径'],
            [[h['phase']+'/'+h['block'],f"w{h['source_worker']} {h['source_node']} GPU{h['source_gpu']} NUMA{h['source_numa']}",h['target_worker'],h['nbytes']/MIB,h['kind']] for h in helper_routes]), '',
        'NUMA表引用实际mbind、触页、256页采样与HIP注册记录；历史分配条目用于说明曾成功分配的内容，当前驻留状态以最终Catalog为准。helper同时有实际source角色执行记录、4MiB计数与对应底本SHA，C3剩余b7由node27 SSD提供。', '',
        '## 精确清理与可追溯性', '',
        '每配置结束只退役该case的optional/target，八个底本在四次cleanup后均保持VALID且lease为0。run结束后按显式许可退役本轮完整SSD底本，最终Catalog和transactions为空，unadopted_shards为空；三节点九个本任务worker/SSD进程及其process group均absent/empty，任务端口已释放。隔离容器保留一个idle init进程，pids.current=1。', '',
        table(['节点','本任务group','任务端口','容器pids','清理后cgroup MiB'],
            [[node,'空','已释放',v['container_pids_current'],f"{v['poststop_cgroup_memory_bytes']/MIB:.3f}"] for node,v in stopped.items()]), '',
        f'独立解析核对{sha_operations}个完整SHA op，累计核验逻辑字节{verified_bytes/MIB:g}MiB，每个op分别哈希source与target。完整输入SHA、实机加载source SHA、各阶段原时间戳和23条资格明细见同目录summary.json。解析器 `{Path(__file__).resolve()}`，SHA `{digest(__file__)}`。']
    (out/'report.md').write_text('\n'.join(lines)+'\n')
    write(out/'artifact_shas.json',dict(parser=dict(path=str(Path(__file__).resolve()),sha256=digest(__file__)),
                                     report=dict(path=str(out/'report.md'),sha256=digest(out/'report.md')),
                                     summary=dict(path=str(out/'summary.json'),sha256=digest(out/'summary.json'))))
    print(json.dumps(dict(status=summary['status'],cases=len(cases),qualification_paths=len(qualification_rows),
        sha_operations=sha_operations,checked_mib=verified_bytes/MIB,restore_published_ms={c['phase']:c['restore_timing_ms']['published_ms'] for c in cases},output=str(out))))


if __name__=='__main__':
    main()
