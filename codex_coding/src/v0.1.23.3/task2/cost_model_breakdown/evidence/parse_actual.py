"""Reconstruct observed breakdowns from immutable local run evidence only."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

MIB = 1 << 20
RECIPES = ('SSD_only', 'C0', 'C1', 'C3')
NODE_NAMES = {'n0': 'node28', 'n1': 'node30', 'n2': 'node27'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |',
                      '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                     ['| ' + ' | '.join(map(str, row)) + ' |' for row in rows])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    actual = repo / 'codex_coding/results/v0.1.23.3/task2/multitier_runtime/operations_actual_r01'
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    references = {}

    def read(path, json_data=True):
        path = path.resolve()
        references[str(path.relative_to(repo))] = {'sha256': sha(path)}
        data = path.read_text()
        return json.loads(data) if json_data else data

    sources = read(actual / 'cases/source_shas.json')
    for name, expected in sources.items():
        path = repo / name.split('/fluidckpt_zjn/', 1)[1]
        read(path, False)
        require(sha(path) == expected, f'Executed source differs: {path}')
    source_manifest = read(actual.parent / 'operations_source_r02/source_manifest.json')
    source_manifest_by_path = {x['relative_path']: x['sha256'] for x in source_manifest['assets']}
    for relative in ('main_lib/fluidckpt/serving/keeper.py',
                     'main_lib/fluidckpt/cluster/service_cost.py'):
        path = repo / relative
        read(path, False)
        require(sha(path) == source_manifest_by_path[relative], f'Deployed source differs: {relative}')
    previous_summary = read(actual.parent / 'runner_result_r01/summary.json')
    read(actual.parent / 'runner_result_r01/report.md', False)
    read(repo / 'docx/plans/2026-10-10_three_node_multitier_scenario.md', False)
    read(repo / 'docx/experiments/2026-10-04_v0.1.23.3_task2_paper_e1_e8_records.md', False)

    log_rows = {}
    for node, workers in [('node28', (0, 1)), ('node30', (2, 3)), ('node27', (4, 5))]:
        for worker in workers:
            path = actual / f'artifacts_{node}/raw/operations_process_multitier_runtime_r02_w{worker}/child.log'
            rows = []
            for line in read(path, False).splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get('ev') in ('cluster_op_begin', 'cluster_op_end'):
                    rows.append(row)
            log_rows[worker] = rows

    recipes = {}
    clock_domains = set()
    for recipe in RECIPES:
        case = read(actual / f'cases/{recipe}.json')
        descriptors = {x['id']: x for x in case['base_descriptors'] + case['owned_descriptors']}
        worker_meta = {x['worker']: {k: x[k] for k in ('worker', 'node', 'gpu', 'numa', 'epoch')}
                       for x in case['workers']}
        item = {'workers': worker_meta, 'phases': {}, 'ssd_io_observed_save_restore': case['ssd_io']}
        phase_io = {}
        for phase in ('save', 'restore'):
            transfer = case[phase]
            if transfer is None:
                item['phases'][phase] = None
                phase_io[phase] = {node: {'transfer_bytes': 0, 'source_sha_read_bytes': 0,
                                         'server_kernel_read_bytes': None} for node in NODE_NAMES.values()}
                continue
            stamps = transfer['controller_timing']
            clock_domains.add(stamps['clock_domain'])
            begin, filled, published = [stamps[k] for k in (
                'load_begin_ns', 'all_targets_filled_ns', 'all_targets_published_ns')]
            require(begin <= filled <= published, f'{recipe}/{phase}: clock order')
            timing = {name: transfer[key] * 1000 for name, key in (
                ('inventory_ms', 'inventory_rpc_s'), ('prepare_rpc_ms', 'prepare_rpc_s'),
                ('execute_ms', 'execute_wall_s'), ('verify_publish_float_ms', 'verify_and_publish_s'))}
            timing.update(filled_ms=(filled - begin) / 1e6,
                          verify_publish_ms=(published - filled) / 1e6,
                          published_ms=(published - begin) / 1e6)
            timing['controller_decomposition_rounding_residual_ms'] = (
                timing['filled_ms'] - timing['inventory_ms'] - timing['prepare_rpc_ms'] - timing['execute_ms'])
            require(abs(timing['controller_decomposition_rounding_residual_ms']) < .01,
                    f'{recipe}/{phase}: timing decomposition mismatch')
            ops = transfer['plan']['ops']
            records = defaultdict(dict)
            worker_execution = []
            dependencies = []
            for worker in transfer['execution']:
                wid = worker['worker']
                total = sum(r['elapsed_s'] for r in worker['records'])
                require(total <= worker['execute_s'] + 1e-6, 'Op duration exceeds worker duration')
                ids = [r['op_id'] for r in worker['records']]
                expected_ids = [op['op_id'] for op in ops if wid in (op['src_w'], op['dst_w'])]
                require(ids == expected_ids, 'Actual op order differs from plan')
                events = [e for e in log_rows[wid] if e.get('tx') == transfer['tx']]
                require([(e['ev'], e['op_id']) for e in events] ==
                        [(ev, oid) for oid in ids for ev in ('cluster_op_begin', 'cluster_op_end')],
                        f'{recipe}/{phase}/w{wid}: operation log order mismatch')
                for first, second in zip(ids, ids[1:]):
                    dependencies.append({'worker': wid, 'before_op': first, 'after_op': second})
                for record in worker['records']:
                    records[record['op_id']][str(wid)] = {
                        'worker': wid, 'role': record['role'], 'elapsed_ms': record['elapsed_s'] * 1000}
                worker_execution.append({'worker': wid, 'execute_ms': worker['execute_s'] * 1000,
                                         'op_elapsed_sum_ms': total * 1000,
                                         'worker_non_op_residual_ms': (worker['execute_s'] - total) * 1000,
                                         'op_order': ids, 'log_order_verified': True})
            largest = max(worker_execution, key=lambda x: x['execute_ms'])
            timing.update(max_worker_execute_ms=largest['execute_ms'],
                          max_worker=largest['worker'],
                          controller_minus_max_worker_residual_ms=timing['execute_ms'] - largest['execute_ms'])
            io = {node: {'transfer_bytes': 0, 'source_sha_read_bytes': 0,
                         'server_kernel_read_bytes': None} for node in NODE_NAMES.values()}
            op_rows = []
            source_bytes = {'hbm': 0, 'dram': 0, 'ssd': 0}
            for op in ops:
                source = descriptors[op['replica_id']]
                source_bytes[source['tier']] += op['n']
                verify = transfer['verification']['operations'][str(op['op_id'])]
                require(verify['source_sha256'] == verify['target_sha256'] and verify['n'] == op['n'],
                        'Source/target SHA mismatch')
                if source['tier'] == 'ssd':
                    io[source['storage_node']]['transfer_bytes'] += op['n']
                    io[source['storage_node']]['source_sha_read_bytes'] += op['n']
                row = {k: op[k] for k in ('op_id', 'replica_id', 'src_w', 'dst_w', 'kind', 'path', 'n')}
                row.update(source_tier=source['tier'], source_node=worker_meta[op['src_w']]['node'],
                           target_node=worker_meta[op['dst_w']]['node'],
                           worker_records=records[op['op_id']], source_sha256=verify['source_sha256'],
                           storage_node=source.get('storage_node'),
                           store_endpoint=source.get('store_endpoint'))
                op_rows.append(row)
            rpc = {}
            for rpc_phase, rows in transfer['rpc'].items():
                rpc[rpc_phase] = [{'worker': wid, **row,
                                   'total_ms': 1000 * sum(row[k] for k in (
                                       'serialize_s', 'transport_and_server_s', 'deserialize_s'))}
                                  for wid, row in enumerate(rows)]
            item['phases'][phase] = {'tx': transfer['tx'], 'controller_timestamps': stamps,
                                    'timing': timing, 'worker_execution': worker_execution,
                                    'ops': op_rows, 'worker_order_dependencies': dependencies,
                                    'source_bytes': source_bytes, 'rpc_controller_observations': rpc,
                                    'plan_estimated_transfer_s': transfer['plan']['estimated_transfer_s'],
                                    'plan_bandwidth_lower_bound_s': transfer['plan']['bandwidth_lower_bound_s']}
            phase_io[phase] = io
        for key, node in NODE_NAMES.items():
            inferred = sum(phase_io[p][node]['transfer_bytes'] + phase_io[p][node]['source_sha_read_bytes']
                           for p in ('save', 'restore'))
            require(inferred == case['ssd_io'][key]['logical_http_read_bytes'], f'{recipe}: SSD read mismatch')
        item['ssd_phase_io_inferred'] = phase_io
        recipes[recipe] = item
    require(len(clock_domains) == 1, 'Case controller domains differ')
    summary = {
        'scope': 'Existing local n=1 functional run; no new GPU, SSH, network or profiling',
        'version': 'v0.1.23.3/task2', 'session': 'multitier_runtime_r02',
        'clock_domains': sorted(clock_domains), 'recipes': recipes,
        'interpretation': {
            'op_elapsed': 'Local wall duration includes logging, source read/copy, collective wait, landing and synchronization as applicable; not isolated service',
            'dependencies': 'Per-worker observed op order; collective participation couples peers; no measured cross-worker wait timeline',
            'cross_node_timestamp_subtraction': False,
            'logs': 'Wall-clock t and local monotonic elapsed_s only; wall t is retained in raw evidence, not subtracted here',
            'execute_residual': 'Controller execute minus largest worker execute; durations compared, clocks not aligned; no attribution to network/SSD/control/dispatch',
            'ssd_phase_io': 'Plan/code-derived transfer and source-SHA HTTP reads, checked against measured save+restore counters; phase kernel counters unavailable',
            'verification_publish': 'Aggregate directly measured; no separate verify duration; complete RPC per-request telemetry exists',
            'inventory_root_cause': 'Unmeasured; source candidates only',
            'prediction': 'All four actual plans have null estimated_transfer_s and bandwidth_lower_bound_s; evidence does not validate numerical cost prediction',
        },
        'input_references': references,
        'parser': {'path': str(Path(__file__).resolve().relative_to(repo)), 'sha256': sha(Path(__file__).resolve())},
    }
    summary_path = out / 'summary.json'
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + '\n')

    lines = ['# 三节点六GPU恢复：原件独立breakdown', '',
             '本解析只读 `operations_actual_r01`，session为`multitier_runtime_r02`。四配方各n=1，冻结布局和暂停配额不同。下表恢复计时来自同一个controller时钟；inventory、prepare RPC与execute用原浮点阶段字段，filled/published及verify+publish用原ns事件，组合差小于0.01ms。外部trusted gate、binding和模型handoff不在窗内。', '',
             table(['配方', 'inventory ms', 'prepare RPC ms', 'execute ms', 'filled ms', 'verify+publish ms', 'published ms'],
                   [[r] + [f"{recipes[r]['phases']['restore']['timing'][k]:.3f}" for k in (
                       'inventory_ms', 'prepare_rpc_ms', 'execute_ms', 'filled_ms', 'verify_publish_ms', 'published_ms')]
                    for r in RECIPES]), '',
             'verify+publish包含每个操作源与目标的全量SHA及complete发布。原件没有单独verify墙钟，complete每worker RPC记录不能相加或直接从总窗扣出验证时间。', '',
             '## 执行窗口与worker局部计时', '',
             table(['配方', 'controller execute ms', '最大worker/execute ms', '两者差值 ms'],
                   [[r, f"{recipes[r]['phases']['restore']['timing']['execute_ms']:.3f}",
                     f"w{recipes[r]['phases']['restore']['timing']['max_worker']}/{recipes[r]['phases']['restore']['timing']['max_worker_execute_ms']:.3f}",
                     f"{recipes[r]['phases']['restore']['timing']['controller_minus_max_worker_residual_ms']:.3f}"] for r in RECIPES]), '',
             '差值只记residual。controller窗口包围六路execute RPC，worker execute从局部执行入口到返回；两者起止不同。该差值不能单独归因于RPC、dispatch、网络或SSD。worker execute减该worker op.elapsed和的局部residual另存在JSON。', '',
             'op.elapsed从开始日志前采时到每op同步后采时，包含适用的HTTP SSD读取、host/GPU拷贝、NCCL collective等待、target落地及同步。源/目标会在不同时刻进入同一op，因此target.elapsed可能含上游等待。现有日志只有wall t和本进程elapsed，未记录各子阶段；不跨节点相减，也不把两端elapsed差解释成等待。', '']
    for recipe in RECIPES:
        phase = recipes[recipe]['phases']['restore']
        lines += [f'### {recipe}实际操作', '',
                  table(['op/块', 'source tier', 'src→dst', 'kind', 'source/both ms', 'target ms'],
                        [[f"{op['op_id']}/{op['replica_id'].split('/')[-1]}", op['source_tier'],
                          f"w{op['src_w']}→w{op['dst_w']}", op['kind'],
                          f"{op['worker_records'][str(op['src_w'])]['elapsed_ms']:.3f}",
                          '同一both记录' if op['src_w'] == op['dst_w'] else
                          f"{op['worker_records'][str(op['dst_w'])]['elapsed_ms']:.3f}"] for op in phase['ops']]), '',
                  '局部顺序：' + '；'.join(f"w{w['worker']}: " + '→'.join(map(str, w['op_order']))
                                         for w in phase['worker_execution'] if w['op_order']) + '。', '']
    lines += [
        'SSD_only的w0先完成b0/b1本地读入，再供b2/b3给w2；w2须接收b2/b3后再从自己的SSD供b4/b5给w4；w4须接收b4/b5后再供b6/b7给w5。w5首个op6 target为51.728ms，内含等上游推进；不能把它当4MiB peer链路时延。该串行参与关系解释尾worker窗口约59.6ms，也说明不能把三个SSD各8/16MiB简单假设全并行。', '',
        'C0、C1只在w0/w2/w4/w5各自顺序填两块；四worker之间可独立推进。C0/C1的首op局部耗时和末op差异较大，现n=1和无子阶段记录不足以识别原因或断言DRAM比HBM快。', '',
        'C3两helper实供：b1为w3/node30/GPU5/HBM→w0/node28/GPU0，source/target为0.740/0.719ms；b2为w1/node28/GPU1/HBM→w2/node30/GPU4，source/target为0.889/2.292ms。b3是w0 DRAM→w2，b5是w2 DRAM→w4，b6为w5本地DRAM。原pair参与顺序使op3要在w0的op0/1、w2的op2后推进，op5在w2的op3及w4的op4后推进，op7在w4的op5及w5的op6后推进；这是结构依赖，尚无去除等待后的数值关键路径。', '',
        'C3尾段b7位于node27 SSD，endpoint=http://12.11.3.27:35402、代理w4/GPU0/NUMA0、目标w5/GPU4/NUMA4。RemoteExtent.read以HTTP读出CPU bytearray/tensor，跨worker代码复制到w4 GPU staging后由45 peer collective传输并落地w5 HBM。w4局部op4→5→7分别0.372/3.129/7.719ms，execute11.411ms；w5 op6→7分别0.968/9.982ms，execute11.038ms。7.719ms是读取加发送及同步整体，9.982ms还受供方前序影响，均不能拆为纯SSD/peer成本。', '',
        '## SSD来源、服务读量与校验读量', '',
        '八SSD底本共32MiB，按node28/30/27分16/8/8MiB。各配置save+restore窗口实测HTTP读取与服务/proc/self/io kernel read_bytes均为64MiB，各node32/16/16MiB。逻辑传输来源与源端SHA再次读取分别列出；下表阶段分配按原plan、verify_run代码推导并核对窗口总量。边界没有kernel counter采样，因此各阶段kernel读量保留null。', '',
        table(['配方', '阶段', 'node28 传输/SHA MiB', 'node30 传输/SHA MiB', 'node27 传输/SHA MiB'],
              [[r, p] + [f"{recipes[r]['ssd_phase_io_inferred'][p][n]['transfer_bytes']/MIB:g}/{recipes[r]['ssd_phase_io_inferred'][p][n]['source_sha_read_bytes']/MIB:g}"
                          for n in NODE_NAMES.values()] for r in RECIPES for p in ('save', 'restore')]), '',
        'C0/C1恢复SSD逻辑来源为0，但save已从SSD读取32MiB建fixture并再次读32MiB校验。C3 save传输28MiB/源SHA28MiB，restore传输4MiB/源SHA4MiB，合计同为64MiB。当前是buffered IO+fadvise记录，不是冷盘读时延试验。save从已有底本建缓存的成本不能代替真实暂停保留已有权重成本。', '',
        '## inventory约62ms：已有证据与候选定位项', '',
        '每次查询并行请求六worker，最大请求transport_and_server字段占大部分窗；JSON序列化/反序列化也有原controller字段。transport_and_server合并urllib请求/响应读取和服务器处理，没有独立网络或服务打点。CPU compact仍返回历史replica和host allocation；RuntimeWorker.inventory仍调用父inventory，查询NUMA meminfo、HIP mem_get_info和allocator计数，再附加HIP BDF、proc/cgroup/sysfs/HCA诊断。runtime_identity每次调用ctypes.util.find_library并CDLL。', '',
        '以上是实际加载源码中的定位候选，不是62ms根因。查询负载、library lookup、HIP API、锁等待、sysfs/proc访问或控制网络分别多少时间，当前均unknown。未运行新profiling，未以源码猜测代替测量。', '',
        '## 可支持的成本模型结论', '',
        '实际来源、串行worker顺序与跨worker前序依赖符合当前执行器语义；SSD fallback增加读取/代理/传输步骤。数值预测精度仍未验收：四原计划estimated_transfer_s与bandwidth_lower_bound_s均为null，没有当前六worker的隔离service参数和独立组合holdout。把本次含等待的op.elapsed反填参数再重放同一次结果属于拟合，不能作为模型验证。inventory和全SHA/发布还必须与data execute分别建账，才对应更广的恢复指标。', '',
        '## 可复现解析与SHA', '',
        '运行（需使用新的输出目录，脚本拒绝覆盖已有结果）：', '',
        '```bash',
        f'python3 {summary["parser"]["path"]} --repo-root {repo} --output codex_coding/results/v0.1.23.3/task2/cost_model_breakdown/evidence_r02',
        '```', '',
        f'本次parser SHA256 `{summary["parser"]["sha256"]}`；summary SHA256 `{sha(summary_path)}`。所有读取原件、部署来源与当前对应源码的SHA保存在summary.input_references；九个关键执行source均与cases/source_shas.json一致，service_cost/keeper与部署manifest一致。源/目标全SHA、六worker原日志op顺序及阶段HTTP读量和窗口总量均核对通过。未复制完整raw、未修改产品/原结果。', '',
        '执行来源：', '',
        table(['源码', '实机source SHA256'], [[Path(p).name, digest] for p, digest in sources.items()]), '',
    ]
    (out / 'report.md').write_text('\n'.join(lines) + '\n')
    (out / 'artifact_shas.json').write_text(json.dumps({
        'parser': summary['parser'], 'summary': {'sha256': sha(summary_path)},
        'report': {'sha256': sha(out / 'report.md')},
    }, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': 'passed', 'output': str(out), 'recipes': len(recipes),
                      'references': len(references), 'summary_sha256': sha(summary_path)}))


if __name__ == '__main__':
    main()
