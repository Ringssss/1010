"""Read-only extraction of complete or failed bounded-session evidence."""
import argparse
import json
from pathlib import Path
from common import native, write, ref, require, fresh_output

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--actual', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    actual = a.actual.resolve()
    out = fresh_output(a.out)
    refs = {}
    def read(path, optional=False):
        path = Path(path)
        if not path.is_absolute():
            path = actual / path
        if optional and not path.exists():
            return None
        value = native.read(path)
        refs[str(path)] = ref(path)
        return value
    session = read('summary.json')
    launch = read(session['plan_ref']['path'])
    startup = read('startup.inventory.json')
    measure = read('measure/summary.json', True)
    mesh_journal = read('mesh/journal.json', True)
    measure_journal = read('measure/journal.json', True)
    final = read('measure/final_inventory.json', True)
    freeze = read('measure/prediction_freeze.json', True)
    frozen = read(launch['frozen_model_ref']['path'])
    postwarm_budget = read('budget_evidence.json',True)
    budget_monitor = read('budget_monitor.json',True) or []
    # This file is only analysis output, never part of the executed frozen assets.
    executed_analysis = out / 'analyse.executed.py'
    executed_analysis.write_bytes(Path(__file__).read_bytes())
    refs[str(executed_analysis)] = ref(executed_analysis)
    operations, budget = {}, {}
    for node in ('node28', 'node30', 'node27'):
        before = read('baseline_' + node + '/stdout.json')
        after = read('after_stop_' + node + '/stdout.json')
        collected = read('exact_collect_' + node + '/stdout.json')
        spec = read(Path(launch['setup_directory']) / (node + '.setup.spec.json'))
        def owners(body):
            return {r['pid']: {k:r[k] for k in ('pid','start_ticks','cgroup','argv_sha256')} for r in body['protected_owners']}
        def containers(body):
            return {r['id']: {k:r[k] for k in ('id','name','StartedAt','Running','Pid')} for r in body['protected_containers']}
        process_rows = []
        sampled_dram = []
        for row in [r for r in launch['processes'] if r['node'] == node]:
            sup = read('artifacts_' + node + '/raw/' + Path(row['out']).name + '/supervisor.summary.json')
            sampled_dram.extend(int(s['memory.current']) for s in sup['samples'] if str(s.get('memory.current','')).isdigit())
            process_rows.append(dict(key=row['key'], supervisor_returncode=session['workers'][row['key']].get('returncode'), child_returncode=sup.get('child_returncode'), summary={k:v for k,v in sup.items() if k != 'samples'}))
        group = [w for w in startup if w['node'] == node]
        budget[node] = dict(dram_sampled_peak_bytes=max(sampled_dram), dram_limit_bytes=6 << 30, dram_reserve_bytes=256 << 20, startup_same_HIP_absolute_hbm_upper_bytes=sum(w['hbm_total_bytes']-w['headroom_bytes']['hbm'] for w in group), hbm_limit_bytes=2 << 30, postwarm_gate_observed=(actual/'budget_evidence.json').exists(), continuous_peak_measured=False)
        hbm = [sample['nodes'][node]['hbm_absolute_upper_bytes'] for sample in budget_monitor]
        if postwarm_budget:
            hbm.append(postwarm_budget['nodes'][node]['hbm_absolute_upper_bytes'])
        budget[node]['postwarm_monitored_same_HIP_absolute_hbm_upper_peak_bytes'] = max(hbm) if hbm else None
        operations[node] = dict(quiet_pids=after['cgroup']['files']['pids.current'], exact_container_unchanged=all(before['container'][k]==after['container'][k] for k in ('Id',)) and before['container']['State']['StartedAt']==after['container']['State']['StartedAt'], protected_owners_unchanged=owners(before)==owners(after), protected_containers_unchanged=containers(before)==containers(after), original_XML_unchanged=before['original_xml_sha256']==after['original_xml_sha256'], frozen_assets_unchanged=all(after['source_assets'].get(row['relative_path'])==row['sha256'] for row in spec['assets']), no_OOM='oom 0\noom_kill 0' in after['cgroup']['files']['memory.events'], exact_collection_passed=collected['passed'], own_children_absent_and_groups_empty=all(not r['current']['live'] and r['group']==[] for r in collected['reports']), protected_owner_count=len(before['protected_owners']), protected_container_count=len(before['protected_containers']), processes=process_rows)
    cases = []
    for label in ('local_novel8', 'mixed_novel8'):
        row = read('measure/' + label + '.json', True)
        if row is None:
            continue
        flow, timing, pred = row['flow'], row['request_timing'], row['prediction']
        ops = [{**r, 'worker':w['worker']} for w in flow['execution'] for r in w['records']]
        worker = max(flow['execution'], key=lambda w:w['execute_s'])
        cases.append(dict(label=label,nbytes=row['nbytes'],operations=row['operations'],actual_service_s=row.get('service_s'),predicted_service_s=pred['estimated_service_s'],service_relative_error=row.get('service_relative_error'),target_worker_s=row['target_s'],maximum_worker=dict(worker=worker['worker'],execute_s=worker['execute_s']),predicted_worker_s=pred['estimated_worker_s'],actual_execute_s=flow['execute_wall_s'],predicted_execute_s=pred['estimated_execute_s'],execute_relative_error=row['execute_relative_error'],actual_full_filled_s=timing['request_to_filled_s'],predicted_full_filled_s=pred['estimated_full_filled_s'],filled_relative_error=row['filled_relative_error'],actual_full_published_s=timing['request_to_published_s'],predicted_full_published_s=pred['estimated_full_published_s'],published_relative_error=row['published_relative_error'],query_before_plan_s=(timing['inventory_end_ns']-timing['request_begin_ns'])/1e9,gate_s=(timing['gate_end_ns']-timing['inventory_end_ns'])/1e9,planning_prediction_s=(timing['plan_end_ns']-timing['gate_end_ns'])/1e9,inner_inventory_s=flow['inventory_rpc_s'],inner_prepare_s=flow['prepare_rpc_s'],verify_publish_s=flow['verify_and_publish_s'],full_clock_domain=timing['clock_domain'],all_byte_SHA=flow['verification'],per_operation=ops,ssd_io=row['ssd_io'],frozen_before_request=freeze['freeze_saved_ns']<timing['request_begin_ns'],control_scope=pred.get('full_filled_prediction_scope'),source_frozen_model_ref=row['source_frozen_model_ref'],bound_model_ref=row['bound_model_ref']))
        cases[-1]['worker_relative_error'] = (pred['estimated_worker_s'] - worker['execute_s']) / worker['execute_s']
        cases[-1]['predicted_service_timeline'] = pred['timeline']
        for node in budget:
            upper = row['gate'][node]['hbm_absolute_upper_bytes']
            previous = budget[node]['postwarm_monitored_same_HIP_absolute_hbm_upper_peak_bytes']
            budget[node]['postwarm_monitored_same_HIP_absolute_hbm_upper_peak_bytes'] = max(upper,previous or 0)
    sha_flows = []
    for label in ('bootstrap0', 'bootstrap2'):
        row = read('measure/' + label + '.json', True)
        if row:
            sha_flows.append(row['verification'])
    sha_flows.extend(c['all_byte_SHA'] for c in cases)
    summary = dict(status=session['status'],failure=session.get('failure'),session=launch['session'],session_elapsed_s=session['elapsed_s'],elapsed_scope='supervisor init through controllers and STOP; not restore latency',training_requests=0,query_AB_requests=0,actual_validation_requests=len(cases),necessary_pair_warm_completed=(actual/'mesh/mesh.json').exists(),bound_model_created=(actual/'measure/bound_model.json').exists(),both_predictions_frozen=freeze is not None,source_model_ref=launch['frozen_model_ref'],source_model_digest=frozen['model_sha256'],cases=cases,verified_bytes=sum(f['checked_bytes'] for f in sha_flows),all_observed_SHA_passed=all(f['passed'] for f in sha_flows) if sha_flows else None,budget=budget,operations=operations,mesh_journal=mesh_journal,measure_journal=measure_journal,final_payload_inventory=final,cleanup_complete=all(o['own_children_absent_and_groups_empty'] and o['quiet_pids']=='1' for o in operations.values()),final_catalog_ssd_verified=final is not None,decision_ready=False,local_service_observed=len(cases)==2,layout_ranking_validated=False,model_T_ready_validated=False,GPU_launches=0,remote_writes=0,claims='Analysis only. A new session can validate migration; it does not certify old-incarnation warm state, shared capacity, placement ranking, or model handoff.')
    local = next((c for c in cases if c['label']=='local_novel8'),None)
    envelope = frozen['model']['paths']['4:hbm:0->4']['empirical_service_range_s']
    summary['local_service_within_training_envelope'] = None if local is None else envelope[0] <= local['actual_service_s'] <= envelope[1]
    write(out/'summary.json',summary)
    lines = ['# 有限成本独立会话：执行结果', '', f"会话 `{launch['session']}` 状态 `{session['status']}`，supervisor 会话总窗 {session['elapsed_s']:.6f}s，含初始化与 STOP，不是恢复延时。新正式请求 {len(cases)} 次，训练/查询 AB 均为 0。", '']
    if not cases:
        error = (mesh_journal or {}).get('failure','')
        if 'worker identities do not match endpoint order' in error:
            lines.extend([f"失败：`{session.get('failure')}`。`mesh_controller/stderr.txt` 首次 `Cluster.inventory()` 报 `worker identities do not match endpoint order`。startup 的六个 `worker` 均为 null；mesh 尚未执行，编号需由 mesh 初始化。新 runner 把原先 mesh 后的 Cluster 级 inventory 提前，触发调用顺序错误。", '', '未 warm、未建 synthetic fixture、未迁移绑定模型、未冻结两预测、未执行 holdout。误差、request clocks、SHA 成绩均为空，不能将原训练或 CPU 预测代入。清理的 Cluster 级 final inventory 同样因未初始化 worker rank 而失败，原 journal 保留该错误。', ''])
        else:
            lines.extend([f"失败：`{session.get('failure')}`。没有完成正式测量请求；具体阶段与原异常保留在 mesh/measure journal，误差与正式请求 SHA 成绩不补造。",''])
    else:
        lines.extend(['| 请求 | service 实测/预测 ms | execute 实测/预测 ms | execute误差 | full filled 实测/预测 ms | filled误差 | published实测 ms |', '|---|---:|---:|---:|---:|---:|---:|'])
        for c in cases:
            service = '组合未作可比单值' if c['actual_service_s'] is None else f"{c['actual_service_s']*1000:.6f}/{c['predicted_service_s']*1000:.6f}"
            lines.append(f"| {c['label']} | {service} | {c['actual_execute_s']*1000:.6f}/{c['predicted_execute_s']*1000:.6f} | {c['execute_relative_error']*100:+.2f}% | {c['actual_full_filled_s']*1000:.6f}/{c['predicted_full_filled_s']*1000:.6f} | {c['filled_relative_error']*100:+.2f}% | {c['actual_full_published_s']*1000:.6f} |")
        lines.extend(['', '误差为（预测−实测）/实测。full filled 是旧训练控制中位数的迁移假说；published 正式预测为 null，校验/发布的字节与目标数成本未建模。mixed 每 op 含等待，不能将 source/target 窗相加冒充纯数据 critical path。', ''])
        lines.extend(['| 请求 | max worker ms | max worker预测误差 | 请求前置query ms | inner query ms | prepareRPC ms | verify/publish ms |', '|---|---:|---:|---:|---:|---:|---:|'])
        for c in cases:
            lines.append(f"| {c['label']} | {c['maximum_worker']['execute_s']*1000:.6f} | {c['worker_relative_error']*100:+.2f}% | {c['query_before_plan_s']*1000:.6f} | {c['inner_inventory_s']*1000:.6f} | {c['inner_prepare_s']*1000:.6f} | {c['verify_publish_s']*1000:.6f} |")
        lines.extend(['', '| 请求 / op | worker / role | 实测 elapsed ms | byte数 |', '|---|---|---:|---:|'])
        for c in cases:
            for row in c['per_operation']:
                lines.append(f"| {c['label']} / {row['op_id']} | {row['worker']} / {row['role']} | {row['elapsed_s']*1000:.6f} | {row['bytes']} |")
        lines.extend(['', f"两请求及两 bootstrap 共 {summary['verified_bytes']/(1<<20):.0f}MiB 全字节 SHA。每次请求的两个 preregistration 与原模型/迁移绑定 SHA 都保持，freeze_saved_ns 早于首个 request_begin_ns；完整请求、内层 transfer clock 均采用 controller 单调钟。原 worker duration 只作时长比较，不与 controller 绝对时间戳相减。", ''])
    lines.extend(['| node | DRAM 250ms 采样峰 B | startup同HIP HBM绝对上界 B | quiet pids | exact退出 |', '|---|---:|---:|---:|---|'])
    for node in operations:
        lines.append(f"| {node} | {budget[node]['dram_sampled_peak_bytes']} | {budget[node]['startup_same_HIP_absolute_hbm_upper_bytes']} | {operations[node]['quiet_pids']} | {operations[node]['own_children_absent_and_groups_empty']} |")
    lines.extend(['', '共享 cgroup 仅按 node 统计一次；HBM 不把 sysfs baseline 减 HIP 数据。startup 上界和 DRAM 采样不能证明完整连续峰值。九 supervisor rc0、九 child planned STOP→TERM/-15、PID absent/组空；各 node quiet pids1，旧 owner/container、原 XML 与全部 frozen assets 前后保留。详见 summary 的逐进程与保护核验。'])
    if final:
        lines.extend(['', '最终 Catalog 无有效副本，used/transactions 清零，三 SSD files=0；journal 的 own 对象按 exact epoch/identity/range 回收。', ''])
    lines.extend(['', '当前 decision_ready=false，未验证布局排序、共享容量或 live 模型 T_ready。解析脚本只读取本地原件，无 SSH/GPU/信号/远端写。'])
    (out/'report.md').write_text('\n'.join(lines)+'\n')
    write(out/'evidence_refs.json',list(refs.values()))
    print(json.dumps(dict(status=session['status'],actual_validation_requests=len(cases),report=str(out/'report.md'),cleanup_complete=summary['cleanup_complete'])))

if __name__ == '__main__':
    main()
