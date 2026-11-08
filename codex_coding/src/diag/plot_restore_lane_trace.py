"""Render measured restore operation lanes and readiness skew, inspired by Meld Figure 4."""
import argparse
import json
from pathlib import Path
import statistics


def build_trace(directory):
    summary = json.loads((directory/'summary.json').read_text())
    if summary['status'] != 'passed': raise ValueError('verified restore required')
    events = json.loads((directory/'rank_events.json').read_text())
    operations = json.loads((directory/'operation_events.json').read_text())
    flow = json.loads((directory/'flow.json').read_text())
    origin = summary['origin_controller_monotonic_s']
    sources = {r['id']: r for r in summary['source_directory']}
    plans = {r['op_id']: r for r in flow['plan']['ops']}
    raw = {}
    for row in operations: raw.setdefault((row['worker'], row['op_id']), {})[row['ev']] = row
    offsets, uncertainty = {}, {}
    for node, before in summary['clocks_before'].items():
        a = before['best']; b = summary['clocks_after'][node]['best']
        offsets[node] = (a['remote_minus_controller_ns']+b['remote_minus_controller_ns'])/2e9
        uncertainty[node] = (max(a['half_rtt_ns'], b['half_rtt_ns'])+
            abs(a['remote_minus_controller_ns']-b['remote_minus_controller_ns'])/2)/1e9
    lanes = []
    for rank in range(16):
        rows = events[str(rank)]
        by_name = {e['event']: e for e in rows}
        target = next(t for t in flow['plan']['targets'] if t['tp']==rank)
        worker = target['worker']; node, gpu = summary['members'][worker]
        clock = offsets[node]
        def event_time(name): return by_name[name]['monotonic_ns']/1e9-clock-origin
        wall_minus_mono = statistics.median((r['wall_ns']-r['monotonic_ns'])/1e9 for r in rows)
        spans = []
        for op in flow['plan']['ops']:
            if op['dst_w'] != worker: continue
            pair = raw[(worker, op['op_id'])]
            begin, end = pair['cluster_op_begin'], pair['cluster_op_end']
            start = begin['t']-wall_minus_mono-clock-origin
            stop = end['t']-wall_minus_mono-clock-origin
            if stop < start: raise ValueError('negative operation span')
            source = sources[op['replica_id']]
            kind = 'ssd' if source['tier']=='ssd' else 'remote' if op['kind']=='network' else 'dram'
            spans.append({'op_id':op['op_id'], 'start_s':start, 'end_s':stop,
                'nbytes':op['n'], 'kind':kind, 'source_worker':op['src_w'], 'source_tier':source['tier']})
        if sum(s['nbytes'] for s in spans) != target['n']: raise ValueError('lane coverage mismatch')
        lanes.append({'rank':rank, 'worker':worker, 'node':node, 'gpu':gpu,
            'load_begin_s':event_time('load_begin'), 'exported_s':event_time('target_exported'),
            'execute_begin_s':event_time('target_execute_begin'),
            'last_data_s':max(s['end_s'] for s in spans),
            'copy_complete_event_s':event_time('target_copy_complete'),
            'weights_ready_s':event_time('weights_ready'), 'spans':spans})
    transfer_begin = min(r['execute_begin_s'] for r in lanes)
    all_data = max(r['last_data_s'] for r in lanes)
    ready = max(r['weights_ready_s'] for r in lanes)
    ttft = summary['restore']['request_to_first_token_s']
    for lane in lanes:
        lane['ready_wait_s'] = max(0,all_data-lane['last_data_s'])
    total_wait = sum(r['ready_wait_s'] for r in lanes)
    return {'status':'passed', 'origin':origin, 'lanes':lanes, 'transfer_begin_s':transfer_begin,
        'all_data_s':all_data, 'weights_ready_s':ready, 'first_token_s':ttft,
        'readiness_spread_s':max(r['last_data_s'] for r in lanes)-min(r['last_data_s'] for r in lanes),
        'post_data_wait_rank_seconds':total_wait,
        'post_data_wait_fraction':total_wait/(len(lanes)*(all_data-transfer_begin)),
        'clock_uncertainty_s':uncertainty,
        'scope':'post-data wait divided by rank-count times transfer-phase wall time; not GPU/SM idle fraction',
        'reference':'Meld, DOI 10.1145/3830418.3843857, Figure 4 on PDF page 5',
        'checked_bytes':summary['restore']['verification']['checked_bytes']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    report = build_trace(args.run_dir)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    plt.rcParams.update({'font.family':'DejaVu Sans', 'font.size':9,
        'axes.spines.top':False, 'axes.spines.right':False, 'pdf.fonttype':42, 'ps.fonttype':42})
    colors = {'dram':'#70bd8a', 'remote':'#40b6c4', 'ssd':'#cf8855'}
    fig, ax = plt.subplots(figsize=(8.2,4.8))
    for lane in report['lanes']:
        rank = lane['rank']; bottom = rank-.34
        ax.broken_barh([(max(0,lane['load_begin_s']),lane['execute_begin_s']-max(0,lane['load_begin_s']))],
                       (bottom,.68),facecolors='#e0e4e8',edgecolors='none')
        for kind,color in colors.items():
            spans = [(s['start_s'],s['end_s']-s['start_s']) for s in lane['spans'] if s['kind']==kind]
            ax.broken_barh(spans,(bottom,.68),facecolors=color,edgecolors='none')
        ax.broken_barh([(lane['last_data_s'],lane['ready_wait_s'])],(bottom,.68),
                       facecolors='#fbf3dd',edgecolors='#d9bf7a',hatch='////',linewidth=.2)
        ax.plot(lane['last_data_s'],rank,'|',color='#37474f',markersize=8,markeredgewidth=1)
    for key,label,color in [('all_data_s','All ranks filled','#37474f'),('first_token_s','First correct token','#243e90')]:
        ax.axvline(report[key],color=color,linestyle='--',linewidth=.9)
        ax.text(report[key],16.25,label,rotation=90,ha='center',va='top',fontsize=8,color=color)
    ax.axhline(7.5,color='#999999',linewidth=.7)
    ticks=list(range(16))
    ax.set_yticks(ticks,[f'{r:02d}  {lane["node"][-2:]}:{lane["gpu"]}' for r,lane in enumerate(report['lanes'])])
    ax.set(xlabel='Time since restore request (s)',ylabel='TP rank   node:DCU',
           xlim=(0,report['first_token_s']+.6),ylim=(-.65,17.8))
    ax.grid(axis='x',alpha=.16);ax.set_axisbelow(True)
    title='Full GLM-5.3 restore from three-node DRAM and SSD'
    ax.set_title(title,pad=13)
    legend=[Patch(facecolor='#e0e4e8',label='Layout / prepare'),
        Patch(facecolor=colors['dram'],label='Local DRAM operation'),
        Patch(facecolor=colors['remote'],label='Remote DRAM operation'),
        Patch(facecolor=colors['ssd'],label='SSD-backed operation'),
        Patch(facecolor='#fbf3dd',edgecolor='#d9bf7a',hatch='////',label='Data ready, other ranks pending')]
    fig.legend(handles=legend,loc='lower center',ncol=3,frameon=False,fontsize=8,bbox_to_anchor=(.53,.01))
    annotation=(f'Rank readiness spread: {report["readiness_spread_s"]:.2f} s   |   '
                f'Post-data waiting: {100*report["post_data_wait_fraction"]:.1f}% of transfer rank-time')
    fig.text(.54,.145,annotation,ha='center',fontsize=8)
    fig.subplots_adjust(left=.14,right=.97,top=.9,bottom=.27)
    for ext in ['png','pdf','svg']:fig.savefig(args.run_dir/f'restore_lanes.{ext}',dpi=220)
    (args.run_dir/'timeline_analysis.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ['lanes','origin']},indent=2))


if __name__=='__main__':
    main()
