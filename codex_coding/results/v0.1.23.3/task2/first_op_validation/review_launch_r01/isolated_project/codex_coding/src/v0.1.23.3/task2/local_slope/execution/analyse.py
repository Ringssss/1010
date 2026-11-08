"""Summarize raw local slope phases without fitting or modifying the frozen model."""
import argparse
import json
from pathlib import Path
import statistics
from common import CASES, native, write, ref, require, fresh_output

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--actual', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    out = fresh_output(a.out)
    rows = native.read(a.actual / 'measure/measurements.json')
    require([row['case'] for row in rows] == CASES, 'raw predeclared eight-request matrix missing')
    require(not native.read(a.actual / 'runtime_setup.json').get('CPU_REPLAY_ONLY'), 'CPU replay cannot be summarized as actual GPU timing')
    raw, aggregate = [], {}
    for row in rows:
        worker = row['flow']['execution'][4]
        record = worker['records'][0]
        item = dict(label=row['case']['label'], mode=row['case']['mode'], bytes=row['case']['nbytes'],
                    service_ms=row['service_s']*1000, worker_execute_ms=row['worker_execute_s']*1000,
                    controller_execute_ms=row['flow']['execute_wall_s']*1000,
                    filled_ms=row['request_to_filled_s']*1000, published_ms=row['request_to_published_s']*1000,
                    prepare_metadata=worker['local_slope_prepare'], verification=row['flow']['verification'],
                    raw_ref=ref(a.actual / 'measure' / (row['case']['label'] + '.json')))
        if row['case']['mode'] == 'diagnostic':
            item['diagnosis'] = record['local_slope_diagnosis']
        else:
            item['execute_thread'] = worker['execute_thread']
        raw.append(item)
    for mode in ('native', 'diagnostic'):
        by_size = {}
        for size in (4, 16):
            own = [item for item in raw if item['mode'] == mode and item['bytes'] == size << 20]
            entry = dict(service_ms=[item['service_ms'] for item in own],
                         mean_service_ms=statistics.mean(item['service_ms'] for item in own), observations=2)
            if mode == 'diagnostic':
                phases = own[0]['diagnosis']['host_phases']
                entry['mean_phase_wall_ms'] = {phase:statistics.mean(item['diagnosis']['host_phases'][phase]['wall_ns']/1e6 for item in own) for phase in phases}
                entry['mean_phase_thread_cpu_ms'] = {phase:statistics.mean(item['diagnosis']['host_phases'][phase]['thread_cpu_ns']/1e6 for item in own) for phase in phases}
                entry['gpu_event_elapsed_ms'] = [item['diagnosis']['gpu_event_elapsed_ms'] for item in own]
                entry['mean_event_elapsed_ms'] = statistics.mean(entry['gpu_event_elapsed_ms'])
                entry['mean_post_service_diagnosis_ms'] = statistics.mean(item['diagnosis']['post_service_diagnosis_wall_ns']/1e6 for item in own)
            by_size[str(size)] = entry
        aggregate[mode] = dict(by_size=by_size, service_large_minus_small_ms=by_size['16']['mean_service_ms']-by_size['4']['mean_service_ms'])
    differences = {}
    for size in (4, 16):
        differences[str(size)] = aggregate['diagnostic']['by_size'][str(size)]['mean_service_ms']-aggregate['native']['by_size'][str(size)]['mean_service_ms']
    diagnostic = aggregate['diagnostic']['by_size']
    phase_delta = {phase:diagnostic['16']['mean_phase_wall_ms'][phase]-diagnostic['4']['mean_phase_wall_ms'][phase] for phase in diagnostic['4']['mean_phase_wall_ms']}
    summary = dict(raw=raw, aggregate=aggregate, diagnostic_minus_native_service_ms=differences,
        diagnostic_large_minus_small_phase_ms=phase_delta,
        diagnostic_large_minus_small_event_ms=diagnostic['16']['mean_event_elapsed_ms']-diagnostic['4']['mean_event_elapsed_ms'],
        limitations=['mode blocks confound temporal drift with instrumentation effect', 'diagnostic prepare additionally initializes events and synchronizes device',
            'diagnostic bypasses native land dispatch branches and adds timing observers', 'event interval includes submission gaps; pure kernel duration unmeasured',
            'post-service metadata included in controller/RPC window but excluded from worker execute/service',
            'TID/ident can be reused; compare thread names and known ThreadingHTTPServer lifecycle', 'no new fit, no bandwidth, no model coefficient update',
            'two observations per size and mode; no broad repeat or size-boundary scan'],
        model_updates=0, fits=0, decision_ready=False, root_cause_certified=False, actual_ref=ref(a.actual / 'summary.json'))
    write(out / 'summary.json', summary)
    lines = ['# localHBM 分段诊断原始结果', '', '|mode / size|service 两点 ms|均值 ms|', '|---|---:|---:|']
    for mode, value in aggregate.items():
        for size, item in value['by_size'].items():
            lines.append(f'|{mode} / {size}MiB|'+ '/'.join(f'{n:.6f}' for n in item['service_ms']) + f'|{item["mean_service_ms"]:.6f}|')
    lines += ['', f'原路径16−4MiB差量 {aggregate["native"]["service_large_minus_small_ms"]:.6f}ms；诊断路径 {aggregate["diagnostic"]["service_large_minus_small_ms"]:.6f}ms。',
              '', '|phase|4MiB wall 均值 ms|16MiB wall 均值 ms|大−小 ms|', '|---|---:|---:|---:|']
    for phase, delta in phase_delta.items():
        lines.append(f'|{phase}|{diagnostic["4"]["mean_phase_wall_ms"][phase]:.6f}|{diagnostic["16"]["mean_phase_wall_ms"][phase]:.6f}|{delta:.6f}|')
    lines += ['', f'设备event夹窗大−小 {summary["diagnostic_large_minus_small_event_ms"]:.6f}ms，含copy提交与host间隙，不能当纯kernel耗时。',
        '', 'native原_execute计时边界保持；diagnostic额外prepare事件初始化/同步、展开本地分支及计时器会改变观测。两模式分块顺序将时间漂移与仪表影响混在一起，差量仅诊断。metadata在service外，controller/RPC仍计其成本。',
        '', '本脚本只作读本地原件与无拟合算术；根因与后续干预须独立审查。旧冻结模型未更新，布局排序未认证。']
    (out / 'report.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps(dict(out=str(out), fits=0, model_updates=0)))

if __name__ == '__main__':
    main()
