"""Offline same-size lookup diagnostic; never calibrate from case outcomes."""
import argparse
import hashlib
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
ACTUAL = ROOT / 'codex_coding/results/v0.1.23.3/task2/multitier_runtime/operations_actual_r01'
SIZE = 4 << 20
CHUNK = 32 << 20


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def path_key(src, tier, numa, dst, dst_tier):
    return f'{src}:{tier}:{numa}->{dst}' + (':' + dst_tier if dst_tier != 'hbm' else '')


def target_record(transfer, op_id):
    rows = [r for w in transfer['execution'] for r in w['records']
            if r['op_id'] == op_id and r['role'] in ('target', 'both')]
    assert len(rows) == 1
    return rows[0]


def error(predicted, actual):
    assert actual > 0
    return dict(predicted_s=predicted, actual_s=actual,
                signed_error_s=predicted-actual, relative_error=(predicted-actual)/actual,
                denominator='actual_s')


def train(qualification, mesh):
    workers = qualification['workers']
    lookup, residuals, inventories, prepares = {}, [], [], []
    warmed = {tuple(p) for p in mesh['necessary_pairs']}
    for key, proof in qualification['paths'].items():
        t = proof['transfer']
        assert proof['status'] == 'verified' and proof['nbytes'] == SIZE
        assert t['executor'] == 'serial' and t['plan']['chunk_bytes'] == CHUNK
        assert len(t['plan']['ops']) == 1 and t['verification']['passed']
        assert t['plan']['targets'][0]['tier'] == proof['target_tier']
        src, dst = proof['source_worker'], proof['target_worker']
        assert key == path_key(src, proof['source_tier'], proof['source_numa'], dst, proof['target_tier'])
        assert proof['source_epoch'] == workers[src]['epoch']
        assert proof['target_epoch'] == workers[dst]['epoch']
        assert src == dst or tuple(sorted((src, dst))) in warmed
        record = target_record(t, 0)
        assert record['bytes'] == SIZE
        lookup[key] = dict(service_s=record['elapsed_s'], source_worker=src, target_worker=dst,
                           source_tier=proof['source_tier'], target_tier=proof['target_tier'],
                           source_numa=proof['source_numa'], source_epoch=proof['source_epoch'],
                           target_epoch=proof['target_epoch'], nbytes=SIZE, executor='serial', chunk_bytes=CHUNK)
        residuals.append(max(0., t['execute_wall_s']-max(w['execute_s'] for w in t['execution'])))
        inventories.append(t['inventory_rpc_s'])
        prepares.append(t['prepare_rpc_s'])
    assert len(lookup) == 23
    return dict(kind='single-size lookup diagnostic', calibration_scope='qualification only',
                lookup=lookup, dispatch_s=statistics.median(residuals), request_s=0.,
                request_s_identified=False, inventory_s=statistics.median(inventories),
                prepare_rpc_s=statistics.median(prepares), workers=workers,
                warm_pairs=sorted(warmed), parameter_sample_count=23)


def predict(transfer, case_workers, model):
    plan = transfer['plan']
    assert plan['executor'] == 'serial' and plan['chunk_bytes'] == CHUNK
    for actual, expected in zip(case_workers, model['workers']):
        for key in ('worker', 'node', 'gpu', 'numa', 'session', 'epoch'):
            assert actual[key] == expected[key]
    targets = {t['key']: t for t in plan['targets']}
    ready = {w['worker']: 0. for w in model['workers']}
    timeline = []
    for op in plan['ops']:
        c = model['lookup'][op['path']]
        src, dst = op['src_w'], op['dst_w']
        assert op['n'] == SIZE and c['source_worker'] == src and c['target_worker'] == dst
        assert op['source_epoch'] == c['source_epoch']
        assert targets[op['target']]['tier'] == c['target_tier']
        start = max(ready[src], ready[dst])
        end = start+c['service_s']
        timeline.append(dict(op_id=op['op_id'], path=op['path'], start_s=start, end_s=end,
                             qualification_target_elapsed_s=c['service_s'],
                             case_target_elapsed_s=target_record(transfer, op['op_id'])['elapsed_s']))
        ready[src] = ready[dst] = end
    service = max(ready.values())
    execute = service+model['dispatch_s']
    filled = execute+model['inventory_s']+model['prepare_rpc_s']
    clock = transfer['controller_timing']
    actual_filled = (clock['all_targets_filled_ns']-clock['load_begin_ns'])/1e9
    return dict(operations=len(plan['ops']), timeline=timeline, final_worker_ready_s=ready,
                service_vs_worker_envelope=error(service, max(w['execute_s'] for w in transfer['execution'])),
                execute=error(execute, transfer['execute_wall_s']), filled=error(filled, actual_filled),
                actual_inventory_s=transfer['inventory_rpc_s'], actual_prepare_rpc_s=transfer['prepare_rpc_s'],
                predicted_published_s=None, published_not_predicted_reason='SHA/publish cost not calibrated')


def report(result):
    m = result['diagnostic_model']
    lines = ['# 六 worker 成本预测证据与同尺寸诊断', '',
        '本轮实机没有运行前冻结成本预测。runner/adapter 使用 frozen_witness，estimated_transfer_s=None，明确无 rate model/runtime prediction。历史两路径 service 模型的 topology_id、worker 编号、NUMA、路径和拟合尺寸与本轮不符，不能移植后报告正式 out-of-sample 误差。', '',
        '以下是 GPU 实验完成后固定规则的离线 single-size lookup diagnostic。23 条先执行的单操作资格记录作为唯一参数来源，四个 case 的实测时间不进入拟合。它检测同尺寸单路记录向操作组合转移是否吻合，不是原 alpha+beta 成本模型正式校准。', '',
        f"资格经验项：dispatch={m['dispatch_s']*1000:.6f}ms，inventory={m['inventory_s']*1000:.6f}ms，prepare RPC={m['prepare_rpc_s']*1000:.6f}ms；request_s=0 为不可辨识占位，不代表请求无开销。", '',
        '每操作采用 qualification 目标端 target/both elapsed_s，按原 service_cost 的两端 ready 递推；不是全部操作串行相加。Worker elapsed 包含日志、同步、对端等待；disjoint pair 可同时推进。表中误差统一 (预测−实测)/实测，不使用预测作为分母。', '',
        '| case/阶段 | DAG服务预测/worker实测(ms) | execute预测/实测(ms) | execute误差 | filled预测/实测(ms) | filled误差 |',
        '|---|---:|---:|---:|---:|---:|']
    for row in result['transfers']:
        s, e, f = (row[k] for k in ('service_vs_worker_envelope', 'execute', 'filled'))
        lines.append(f"| {row['case']}/{row['stage']} | {s['predicted_s']*1000:.3f} / {s['actual_s']*1000:.3f} | {e['predicted_s']*1000:.3f} / {e['actual_s']*1000:.3f} | {e['relative_error']*100:+.2f}% | {f['predicted_s']*1000:.3f} / {f['actual_s']*1000:.3f} | {f['relative_error']*100:+.2f}% |")
    lines += ['', 'filled 预测只加资格阶段控制项中位数；没有加实际 case 的库存/prepare 时间。约62ms库存项能压低总窗相对误差，不能遮蔽 execute 或服务段误差。published 还含全SHA/发布，未校准所以不给预测。', '',
        '严格匹配已通过：23键包含目的层（非HBM追加 :dram），没有目的层碰撞；四恢复以及三个保存的路径/层/尺寸/executor/chunk均覆盖；session/epoch一致，必要六pair已在资格前 warm。资格与case为同host不同controller进程，完整clock_domain不同；仅每个transfer内部取同钟差。run_session同步等待qualify完成后才调用cases，两controller记录rc0，证明资格先于case；不跨clock_domain用绝对ns相减证明顺序。', '',
        '恢复段中 C0/C3 execute 误差约−23%/+20%，查表还把 C0/C1 的单次 execute 顺序预测反了。C0/C1实际差仅0.605ms且每配置一次，不能认证统计排序，但足以阻止把本诊断宣称为已经验证的排序器。服务DAG与worker envelope误差仍应单看，filled的较小误差不替代它们。', '',
        '适用限制：每路径仅4MiB一个点，不能同时辨识固定项与带宽；本诊断直接查表，没有拟合alpha/beta/gamma，也没有创建正式schema1/2 service_model。若把lookup编码为零固定/零冷项和等效rate，零项仅为同4MiB重现数值的约定，不是已经识别的物理参数，不允许尺寸外推。单操作与7/8操作请求不同，目标4MiB与整rank8MiB不同，loop/request开销未分离。跨worker的目标elapsed包含未对齐启动/对端等待，再按DAG相加可能重复计等待；SSD源文件、pagecache/磁盘状态及组间变化没有控制。n=1不能统计检验排序，不能外推模型规模、冷盘/冷pair、共享HCA容量或live模型T_ready。', '',
        '最小下一步：先用一个无数据控制请求/库存诊断确认约62ms观测成本；成本测量只补目标决策必需路径，固定同worker/incarnation/层/chunk/warm状态，各取两个warm尺寸以分离固定项与字节项（沿用fit_path/fit_request_model）。若请求成本需要单列，增加等总字节、不同操作数记录；冻结参数后仅执行一个独立尺寸或组合holdout。冷pair仅在研究冷启动时单列一次。不要重建模型或扫描104机会。', '',
        '完整每op lookup、DAG时间线、误差精确值、资格经验项、原件SHA在summary.json；规则在源码rules.md。此次仅离线CPU分析，无GPU/SSH/产品修改。']
    return '\n'.join(lines)+'\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    qpath, mpath = ACTUAL/'qualification/qualification.json', ACTUAL/'mesh/mesh.json'
    qualification = read(qpath)
    model = train(qualification, read(mpath))
    inputs = [qpath, mpath, Path(__file__), Path(__file__).with_name('rules.md'),
              ROOT/'main_lib/fluidckpt/cluster/service_cost.py', ROOT/'main_lib/fluidckpt/cluster/path_catalog.py',
              ROOT/'codex_coding/src/v0.1.23.3/task2/multitier_runtime/runner/run.py',
              ROOT/'codex_coding/src/v0.1.23.3/task2/multitier_scenarios/adapter/adapter.py']
    result = dict(status='complete', formal_pre_run_prediction_found=False, formal_model_error=None,
                  analysis_type='post-run offline single-size lookup diagnostic', diagnostic_model=model, transfers=[])
    qcommand_path = ACTUAL/'qualification_controller/command.json'
    ccommand_path = ACTUAL/'cases_controller/command.json'
    parent_path = ROOT/'codex_coding/src/v0.1.23.3/task2/multitier_runtime/operations/run_session.py'
    inputs += [qcommand_path, ccommand_path, parent_path]
    qcommand, ccommand = read(qcommand_path), read(ccommand_path)
    assert qcommand['returncode'] == ccommand['returncode'] == 0
    assert qcommand['argv'][qcommand['argv'].index('--stage')+1] == 'qualify'
    assert ccommand['argv'][ccommand['argv'].index('--stage')+1] == 'cases'
    assert ccommand['argv'][ccommand['argv'].index('--qualification')+1] == str(qpath)
    qdomains = {p['transfer']['controller_timing']['clock_domain'] for p in qualification['paths'].values()}
    cdomains = set()
    for case in ('SSD_only', 'C0', 'C1', 'C3'):
        path = ACTUAL/f'cases/{case}.json'
        inputs.append(path)
        data = read(path)
        for stage in ('save', 'restore'):
            if data[stage] is None:
                continue
            cdomains.add(data[stage]['controller_timing']['clock_domain'])
            row = predict(data[stage], data['workers'], model)
            result['transfers'].append(dict(case=case, stage=stage, **row))
    assert len(qdomains) == len(cdomains) == 1
    result['chronology'] = dict(qualification_precedes_cases=True,
        basis='run_session synchronous qualify controller returns before cases controller starts; both command returncode=0',
        qualification_clock_domain=next(iter(qdomains)), cases_clock_domain=next(iter(cdomains)),
        cross_controller_ns_difference_used=False)
    result['artifact_shas'] = {str(p.relative_to(ROOT)): sha(p) for p in inputs}
    (args.out/'summary.json').write_text(json.dumps(result, indent=2, sort_keys=True)+'\n')
    (args.out/'report.md').write_text(report(result))
    print(json.dumps(dict(status='complete', out=str(args.out), transfers=len(result['transfers']),
                         dispatch_ms=model['dispatch_s']*1000)))


if __name__ == '__main__':
    main()
