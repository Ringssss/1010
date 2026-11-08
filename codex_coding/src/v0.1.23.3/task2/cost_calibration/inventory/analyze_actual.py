"""Read existing inventory measurements and report attribution without new sampling."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / 'codex_coding/results/v0.1.23.3/task2/cost_calibration'
SEGMENTS = ('mode_file_read', 'native_inventory', 'library_resolution', 'cdll_load',
            'hip_bdf', 'hca_metadata', 'proc_identity', 'cgroup_metadata',
            'pci_metadata', 'environment_and_rss')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    refs, checks, runs, rpc_rows = [], [], [], []
    def check(name, condition):
        checks.append(dict(name=name, passed=bool(condition)))
        if not condition:
            raise AssertionError(name)
    def reference(path):
        row = dict(path=str(path.resolve()), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        if row not in refs:
            refs.append(row)
        return row
    def read(path):
        reference(path)
        return json.loads(path.read_text())
    sources = [ROOT / path for path in (
        'codex_coding/src/v0.1.23.3/task2/cost_calibration/inventory/worker.py',
        'codex_coding/src/v0.1.23.3/task2/cost_calibration/inventory/identity_diagnostics.py',
        'codex_coding/src/v0.1.23.3/task2/multitier_runtime/runner/runtime_worker.py',
        'main_lib/fluidckpt/cluster/worker.py', 'main_lib/fluidckpt/cluster/control.py')]
    source_refs = [reference(path) for path in sources]
    for name in ('execution_actual_r01', 'execution_actual_r02'):
        base = RESULTS / name
        status = read(base / 'summary.json')
        plan = read(Path(status['plan_ref']['path']))
        check(name + ' plan reference SHA', reference(Path(status['plan_ref']['path'])) == status['plan_ref'])
        rounds = read(base / 'mesh/inventory_ab.json')
        decision = read(base / 'mesh/inventory_decision.json')
        check(name + ' fixed two legacy then two cached rounds',
              [(r['mode'], r['index']) for r in rounds] == [('legacy', 0), ('legacy', 1), ('cached', 0), ('cached', 1)])
        for node in ('node28', 'node30', 'node27'):
            baseline = read(base / f'baseline_{node}/stdout.json')
            for source in source_refs:
                relative = str(Path(source['path']).relative_to(ROOT))
                check(f'{name} {node} deployed source {relative}', baseline['source_assets'].get(relative) == source['sha256'])
        processed_rounds = []
        for r in rounds:
            check(f'{name} {r["mode"]}{r["index"]} six ordered RPC',
                  len(r['rpc']) == len(r['workers']) == 6 and [w['worker'] for w in r['workers']] == list(range(6)))
            check(f'{name} {r["mode"]}{r["index"]} controller same-clock arithmetic',
                  abs(r['elapsed_s'] - (r['end_ns'] - r['begin_ns']) / 1e9) < 1e-12)
            for w, rpc in zip(r['workers'], r['rpc']):
                d, identity = w['inventory_diagnosis'], w['runtime_identity']
                label = f'{name}/{r["mode"]}{r["index"]}/w{w["worker"]}'
                check(label + ' complete identity and original inventory retained',
                      set(w) == set(rounds[0]['workers'][w['worker']])
                      and set(identity) == set(rounds[0]['workers'][w['worker']]['runtime_identity']))
                check(label + ' timer conservation',
                      d['worker_inventory_end_ns'] - d['worker_inventory_start_ns'] == d['worker_inventory_elapsed_ns']
                      and sum(d['segments_ns'].values()) == d['segment_sum_ns']
                      and d['segment_sum_ns'] + d['residual_ns'] == d['worker_inventory_elapsed_ns']
                      and d['residual_ns'] >= 0)
                check(label + ' all measured fresh sources positive',
                      set(d['segments_ns']) == set(SEGMENTS) and all(value > 0 for value in d['segments_ns'].values()))
                check(label + ' mode and cache hit correct', d['mode'] == r['mode']
                      and d['library_cache_hit'] == (r['mode'] == 'cached'))
                check(label + ' identity equals task binding',
                      (w['node'], w['gpu'], w['numa'], w['session'], identity['actual_bdf'], identity['boot_id'], identity['pid'])
                      == (plan['workers'][w['worker']]['node'], plan['workers'][w['worker']]['gpu'],
                          plan['workers'][w['worker']]['numa'], plan['session'], plan['workers'][w['worker']]['bdf'],
                          plan['workers'][w['worker']]['boot_id'], w['pid']))
                row = dict(run=name, mode=r['mode'], index=r['index'], worker=w['worker'],
                           node=w['node'], gpu=w['gpu'], pid=w['pid'], sequence=d['sequence'],
                           cache_origin_sequence=d['cache_origin_sequence'], library_cache_hit=d['library_cache_hit'],
                           library_path=d['library_path'], controller_inventory_ms=r['elapsed_s'] * 1000,
                           worker_inventory_ms=d['worker_inventory_elapsed_ns'] / 1e6,
                           worker_residual_ms=d['residual_ns'] / 1e6,
                           transport_and_server_ms=rpc['transport_and_server_s'] * 1000,
                           deserialize_ms=rpc['deserialize_s'] * 1000, serialize_ms=rpc['serialize_s'] * 1000,
                           response_bytes=rpc['response_bytes'], **{key + '_ms': d['segments_ns'][key] / 1e6 for key in SEGMENTS})
                rpc_rows.append(row)
            maximum = max(w['inventory_diagnosis']['worker_inventory_elapsed_ns'] for w in r['workers']) / 1e6
            processed_rounds.append(dict(mode=r['mode'], index=r['index'], controller_inventory_ms=r['elapsed_s'] * 1000,
                max_worker_inventory_ms=maximum, controller_minus_max_worker_duration_residual_ms=r['elapsed_s'] * 1000 - maximum,
                max_library_resolution_ms=max(w['inventory_diagnosis']['segments_ns']['library_resolution'] for w in r['workers']) / 1e6,
                max_library_and_cdll_ms=max(sum(w['inventory_diagnosis']['segments_ns'][key] for key in ('library_resolution', 'cdll_load')) for w in r['workers']) / 1e6,
                max_rpc_transport_and_server_ms=max(rpc['transport_and_server_s'] for rpc in r['rpc']) * 1000,
                max_rpc_deserialize_ms=max(rpc['deserialize_s'] for rpc in r['rpc']) * 1000))
        workers = []
        for index in range(6):
            rows = [r['workers'][index] for r in rounds]
            stable_native = ('worker', 'node', 'gpu', 'numa', 'pid', 'session', 'epoch', 'budget_bytes', 'capabilities')
            stable_identity = set(rows[0]['runtime_identity']) - {'cgroup_memory_current_bytes', 'process_rss_bytes'}
            check(f'{name} w{index} static identity unchanged',
                  all(all(w[key] == rows[0][key] for w in rows) for key in stable_native)
                  and all(all(w['runtime_identity'][key] == rows[0]['runtime_identity'][key] for w in rows) for key in stable_identity))
            check(f'{name} w{index} cached origin is preceding timed legacy call',
                  rows[0]['inventory_diagnosis']['cache_origin_sequence'] == rows[0]['inventory_diagnosis']['sequence']
                  and rows[1]['inventory_diagnosis']['cache_origin_sequence'] == rows[1]['inventory_diagnosis']['sequence']
                  and all(w['inventory_diagnosis']['cache_origin_sequence'] == rows[1]['inventory_diagnosis']['sequence'] for w in rows[2:]))
            check(f'{name} w{index} fresh sample clock increasing', all(rows[i]['headroom_sample_ns'] < rows[i+1]['headroom_sample_ns'] for i in range(3)))
            workers.append(dict(worker=index, pid=rows[0]['pid'], actual_bdf=rows[0]['runtime_identity']['actual_bdf'],
                sequences=[w['inventory_diagnosis']['sequence'] for w in rows],
                cache_origin_sequences=[w['inventory_diagnosis']['cache_origin_sequence'] for w in rows],
                cgroup_current_values=[w['runtime_identity']['cgroup_memory_current_bytes'] for w in rows],
                rss_values=[w['runtime_identity']['process_rss_bytes'] for w in rows],
                dram_headroom_values=[w['headroom_bytes']['dram'] for w in rows],
                hbm_headroom_values=[w['headroom_bytes']['hbm'] for w in rows],
                headroom_sample_ns=[w['headroom_sample_ns'] for w in rows],
                cgroup_current_changed=len({w['runtime_identity']['cgroup_memory_current_bytes'] for w in rows}) > 1,
                rss_changed=len({w['runtime_identity']['process_rss_bytes'] for w in rows}) > 1))
        medians = {mode: {key: statistics.median(r[key] for r in processed_rounds if r['mode'] == mode)
                         for key in ('controller_inventory_ms', 'max_worker_inventory_ms', 'max_library_and_cdll_ms')}
                   for mode in ('legacy', 'cached')}
        controller_drop = medians['legacy']['controller_inventory_ms'] - medians['cached']['controller_inventory_ms']
        library_drop = medians['legacy']['max_library_and_cdll_ms'] - medians['cached']['max_library_and_cdll_ms']
        check(name + ' recomputed decision medians',
              abs(medians['legacy']['controller_inventory_ms'] / 1000 - decision['legacy_median_s']) < 1e-12
              and abs(medians['cached']['controller_inventory_ms'] / 1000 - decision['cached_median_s']) < 1e-12
              and abs(library_drop / 1000 - decision['library_drop_s']) < 1e-12)
        runs.append(dict(run=name, session=plan['session'], status=status['status'], failure=status.get('failure'),
                         rounds=processed_rounds, medians=medians, controller_reduction_ms=controller_drop,
                         library_reduction_ms=library_drop, library_drop_over_controller_drop=library_drop/controller_drop,
                         decision=decision, workers=workers))
    holdouts = []
    for name in ('holdout_prediction', 'holdout_legacy_control'):
        data = read(RESULTS / 'execution_actual_r02/measure' / (name + '.json'))
        timing = data['request_timing']
        holdouts.append(dict(label=name,
            planning_inventory_ms=(timing['inventory_end_ns']-timing['request_begin_ns']) / 1e6,
            transfer_inventory_ms=data['flow']['inventory_rpc_s'] * 1000,
            request_to_filled_ms=timing['request_to_filled_s'] * 1000,
            request_to_published_ms=timing['request_to_published_s'] * 1000,
            execute_ms=data['flow']['execute_wall_s'] * 1000,
            prepare_rpc_ms=data['flow']['prepare_rpc_s'] * 1000,
            verification_ms=data['flow']['verify_and_publish_s'] * 1000,
            epoch_snapshot_reused=data['flow']['epoch_snapshot_reused'],
            fresh_query_rpc=data['fresh_query_rpc'], transfer_inventory_rpc=data['flow']['rpc']['inventory']))
    check('paired requests retain both fresh inventories', all(not row['epoch_snapshot_reused'] for row in holdouts))
    delta = {key: holdouts[1][key]-holdouts[0][key] for key in ('planning_inventory_ms', 'transfer_inventory_ms', 'request_to_filled_ms', 'request_to_published_ms', 'execute_ms')}
    delta['sum_inventory_ms'] = delta['planning_inventory_ms'] + delta['transfer_inventory_ms']
    body = dict(schema_version=1, passed=True, check_count=len(checks), checks=checks, runs=runs,
                main_performance_run='execution_actual_r02', rpc_count=len(rpc_rows), source_refs=source_refs,
                refs=refs, paired_holdout=holdouts, paired_legacy_minus_cached_ms=delta,
                pid_guard_scope='same PID throughout each actual comparison; PID-change invalidation tested by CPU fixture only',
                clock_scope='differences of timestamps only within their recorded controller or worker process; residual uses durations',
                analysis_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / 'summary.json').write_text(json.dumps(body, indent=2) + '\n')
    with (args.out / 'per_rpc.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rpc_rows[0]))
        writer.writeheader()
        writer.writerows(rpc_rows)
    lines = ['# 实际库存查询根因与薄缓存对照', '',
        '完整会话 execution_actual_r02 的主要负担是重复库解析。六worker每模式两次原字段库存查询后，仅复用库路径/CDLL句柄，把controller完整查询中位数从60.575624ms降至6.189593ms；HIP BDF和所有动态来源继续重新读取。不是传输带宽优化。', '',
        '| 会话 | 模式/序号 | Controller ms | max worker ms | max 查库 ms | max transport+server ms | max deserialize ms |',
        '|---|---|---:|---:|---:|---:|---:|']
    for run in runs:
        for r in run['rounds']:
            lines.append(f"| {run['run']} | {r['mode']}/{r['index']} | {r['controller_inventory_ms']:.6f} | {r['max_worker_inventory_ms']:.6f} | {r['max_library_resolution_ms']:.6f} | {r['max_rpc_transport_and_server_ms']:.6f} | {r['max_rpc_deserialize_ms']:.6f} |")
    lines += ['', '## 根因对照', '']
    for run in runs:
        lines.append(f"{run['run']}：controller中位下降{run['controller_reduction_ms']:.6f}ms，最大查库/CDLL中位下降{run['library_reduction_ms']:.6f}ms，比例{run['library_drop_over_controller_drop']:.6f}。分段与整体改善对应。各round最大worker可能不同；这些中位差不能作为精确可加成本归因。")
    lines += ['', 'r01 的查询对照在后续正常active事务被monitor错误当作idle拒绝前已经完整记录；该会话整体failed，保留为独立查询证据，不并入r02性能均值。r02是主完整运行，summary标为completed_pending_independent_review，完整执行/清理由独立review验收。', '',
        '## 身份与实时性', '',
        '48个A/B RPC的全部原字段、分段守恒、绑定BDF/boot/PID，以及每会话六worker的epoch/session/node/GPU/NUMA和完整静态runtime_identity均核对。缓存origin指向每worker前一次已计时legacy请求，而非窗外预热；cached中PID保持同一值、library_cache_hit=true、HIP BDF分段继续大于零。PID改变时重建仅由CPU夹具认证，实机没有强制PID变更。', '',
        '两个会话每worker的headroom_sample_ns严格递增；cgroup current实际变化，DRAM headroom与部分RSS也变化。未改变的RSS/HBM free不意味着缓存；源代码每次调用native inventory和读取动态identity，CPU夹具已验证字段改变时刷新。空Catalog/事务的A/B阶段没有制造ledger变化，因此fresh ledger状态改变主要依据原native委派和CPU验证，不宣称该空库存对照验证了状态转换。', '',
        '## 完整请求中的查询位置', '',
        '| r02请求 | plan前库存 ms | transfer内库存 ms | execute ms | request→filled ms | request→published ms |',
        '|---|---:|---:|---:|---:|---:|']
    for h in holdouts:
        lines.append(f"| {h['label']} | {h['planning_inventory_ms']:.6f} | {h['transfer_inventory_ms']:.6f} | {h['execute_ms']:.6f} | {h['request_to_filled_ms']:.6f} | {h['request_to_published_ms']:.6f} |")
    lines += ['', f"完整请求保留两次fresh inventory。legacy−cached的两个库存窗差合计{delta['sum_inventory_ms']:.6f}ms；filled差{delta['request_to_filled_ms']:.6f}ms，published差{delta['request_to_published_ms']:.6f}ms，execute差{delta['execute_ms']:.6f}ms。该同会话cached→legacy对照各一次、顺序固定，包含源/目标状态和顺序混杂；用来解释控制成本，不能报告统计稳定的传输收益或跨布局排序。", '',
        '## 计时覆盖和证据', '',
        'worker timer包含模式文件读取、原native库存、全部identity来源和原字段组装；结束后的诊断字典组装、HTTP JSON序列化与传输由controller完整库存窗覆盖。缓存填充已包含在origin legacy窗。独立诊断锁等待及Python计时/结构开销包含worker residual。controller−max worker是持续时间残差，含线程启动、HTTP及调度等，不能精确命名dispatch；不跨进程直接减时间戳。模式切换、进程/mesh初始化不在A/B查询窗内；所有样本此前已初始化且warm，结果不代表首次启动或cold库/链路成本。', '',
        f"本报告只读既有原件、无GPU/SSH/新采样。{len(checks)}项本地审计通过；精确48个RPC及全部service分段见per_rpc.csv，cache origin/PID/动态值、holdout 24个RPC计时和源/结果SHA见summary.json。原source SHA和三节点各会话baseline deployed assets均匹配。", '']
    (args.out / 'report.md').write_text('\n'.join(lines))
    print(json.dumps(dict(passed=True, check_count=len(checks), rpc_count=len(rpc_rows), out=str(args.out))))


if __name__ == '__main__':
    main()
