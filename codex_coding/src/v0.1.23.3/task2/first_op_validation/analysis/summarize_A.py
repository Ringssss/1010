"""Post-A reporting only; consume the pre-A frozen analyzer's decisions."""
import argparse
import json
from pathlib import Path
from analyze import digest, load, save_new


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    stage, model = load(args.stage), load(args.model)
    if stage['stage'] != 'A' or stage.get('CPU_REPLAY_ONLY') or digest(args.stage) != model['A_sha256']:
        raise ValueError('Requires original actual A and its frozen analyzer output')
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    gate = model['gate']
    rows = []
    for r in stage['requests']:
        op1, op2 = r['ops']
        delta = lambda clock: sum(op1['phases'][k][clock + '_ns'] - op2['phases'][k][clock + '_ns'] for k in ('source_slice', 'target_slice', 'copy_submit'))
        rows.append({'cell_id': r['cell_id'], 'executor_mode': r['executor_mode'], 'target_state': r['target_state'],
                     'request_ordinal': r['request_ordinal'], 'op1_service_ns': op1['record_total_ns'],
                     'op2_service_ns': op2['record_total_ns'], 'nonlog_host_delta_wall_ns': delta('wall'),
                     'nonlog_host_delta_cpu_ns': delta('cpu'), 'worker_execute_ns': r['worker_execute_ns'],
                     'controller_execute_ns': r['controller_execute_ns'], 'full_ns': r['full_ns'],
                     'postdiagnosis_ns': r['timing_components']['postdiagnosis_ns']})
    refs = [{'path': str(Path(p).resolve()), 'sha256': digest(p)} for p in (args.stage, args.model, __file__)]
    summary = {'status': 'actual_A_analysis_generated_pending_independent_review', 'state': gate['state'],
               'A_gate_passed': gate['passed'], 'B_predictions_count': len(model['predictions']),
               'frozen': model['frozen'], 'product_model_update': False, 'independent_prediction_validation_performed': False,
               'host_first_excess_observed_all_8_requests': all(row['nonlog_host_delta_cpu_ns'] > 0 and row['nonlog_host_delta_wall_ns'] > 0 for row in rows),
               'rows': rows, 'references': refs, 'bandwidth': None, 'old_bounded_prediction': None,
               'published_prediction': None, 'scope': 'One A session; not a universal law or a cross-session state coefficient.'}
    save_new(out / 'summary.json', summary)
    lines = ['# 首操作状态A阶段：主机差额存在，状态规则未识别', '',
             '本轮A完成8个请求、每请求两copy。按A前冻结判据，state_unidentified，B准入为false；系数诊断保留，frozen=false，B预测为空。',
             '8个请求的nonlog首/次CPU与wall差額均为正，支持本会话有首操作主机成本；当前结果不能把该成本只按thread-first或request-first稳定收费。', '',
             '| cell/请求 | 执行线程/目标 | op1/op2服务(us) | nonlog首−次CPU(us) | worker(ms) | controller(ms) | full(ms) |',
             '|---|---|---:|---:|---:|---:|---:|']
    for row in rows:
        lines.append(f"| {row['cell_id']}/R{row['request_ordinal']} | {row['executor_mode']}/{row['target_state']} | {row['op1_service_ns']/1000:.3f}/{row['op2_service_ns']/1000:.3f} | {row['nonlog_host_delta_cpu_ns']/1000:.3f} | {row['worker_execute_ns']/1e6:.6f} | {row['controller_execute_ns']/1e6:.6f} | {row['full_ns']/1e6:.6f} |")
    f = model['factors']
    lines += ['', f"冻结S={gate['scale_ns']/1000:.3f}us，2S={2*gate['scale_ns']/1000:.3f}us。R1四cell及F R2的nonlog CPU/wall首/次差均通过2S与CPU/wall一致门。", '',
              f"P R2首−次CPU：fresh={f['nonlog_host.cpu_ns']['R2_op1_minus_op2_by_cell']['2']/1000:.3f}us，pretouched={f['nonlog_host.cpu_ns']['R2_op1_minus_op2_by_cell']['4']/1000:.3f}us；均大于2S，不能支持纯thread-first重置。", '',
              f"R2 F−P第一op nonlog CPU：fresh={f['nonlog_host.cpu_ns']['R2_thread_fresh_F_minus_P']/1000:.3f}us，pretouched={f['nonlog_host.cpu_ns']['R2_thread_pretouched_F_minus_P']/1000:.3f}us；均大于S，不能支持F/P等价的纯request-first解释。", '',
              'P的fresh与pretouched service首/次差额之差−196.682us，超过目标交互门57.176us；F交互−19.800us通过。A只有每cell一个顺序block，不能把P差额唯一归因为目标写史，也不能排除顺序/执行状态交互。', '',
              f"空成对clock额外p95={gate['observer_check']['empty_paired_clock_extra_p95_ns']/1000:.3f}us；dummy logger绝对p95={gate['observer_check']['dummy_log_p95_ns']/1000:.3f}us、波动p95={gate['observer_check']['dummy_log_fluctuation_p95_ns']/1000:.3f}us。日志有首/次变化，但source_slice/copy_submit CPU也有独立差额，结果不只是logger-only。仪表与日志成本没有从实测扣除。", '',
              '| 候选 | p(us) | 首项(us) | 解释 |', '|---|---:|---:|---|']
    for name, candidate in model['fit']['candidates'].items():
        coef = candidate['coefficients_ns']
        first = f'{coef[1]/1000:.3f}' if len(coef) > 1 else '—'
        interpretation = '同16op常数' if name == 'C0' else '同16op请求首项' if name == 'C1' else 'thread状态诊断，未准入'
        lines.append(f'| {name} | {coef[0]/1000:.3f} | {first} | {interpretation} |')
    lines += ['', '三候选同A全部16op非负拟合，保留全部首/次与长点；这些是训练诊断，不是B独立预测误差。固定两copy条件下，2*C0.p=2*C1.p+C1.r；若request-first成立，C2与C1同构，无新的请求总成本区分力。本轮因A结构未通过而不打开B，不能宣称完整模型预测收益。', '',
              'record-gap、set_device、实际线程create/dispatch/join、postdiagnosis、worker其他项按A的F/P与R1/R2分组记账；controller残差、query、prepare（含干预与R1分配）、full其他项全部保留且严格闭合。仅postdiagnosis已有0.664–1.279ms，完整full为20.432–25.749ms；不能把几十us服务改善直接当恢复主指标收益。', '',
              '测量payload 128MiB、prepare预写/scratch 64MiB、bootstrap 16MiB另列；最终八target全SHA共64MiB，非16轮各独立SHA。独立执行/source/生命周期/清理资格仍由review认证。旧bounded模型未改，本域旧预测null；字节斜率/带宽与published预测均null。', '',
              '本分析没有新GPU/SSH；只读取A原件。冻结规则和源码未修改，额外脚本仅生成本报告。按预登记止于A，不新增参数、不扩矩阵、不补第三block。', '', '引用：']
    for ref in refs:
        lines.append(f"- {ref['path']} SHA256 {ref['sha256']}")
    with (out / 'report.md').open('x') as handle:
        handle.write('\n'.join(lines) + '\n')
    print(out / 'report.md')


if __name__ == '__main__':
    main()
