"""Join historical sample records to original rank events without touching live state."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[5]
RESULTS = ROOT/'codex_coding/results'
sys.path.insert(0, str(ROOT/'main_lib'))
from fluidckpt.serving.measure import summarize


def main(args):
    protocol = json.loads(args.protocol.read_text())
    stage = protocol['experiment_id']; out = args.out
    out.mkdir(parents=True, exist_ok=False)
    (out/'protocol.json').write_text(json.dumps(protocol, indent=2))
    used = {}
    def read(path):
        raw = path.read_bytes(); used[str(path.relative_to(ROOT))] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)
    aggregate = read(RESULTS/'motivation/verified_pressure/summary.json')
    names = ['serial_cost09'] if stage == 'e1' else ['cpu_first03', 'cpu_first08']
    cases, parents = [], {}
    for name in names:
        data = read(RESULTS/'motivation'/name/'summary.json')
        parents[name] = {'status': data['status'], 'error': data.get('error'), 'protocol': data.get('protocol')}
        recalls = data['lifecycle']['recalls']
        for sample in data['samples']:
            matches = [r for r in recalls if r.get('flow', {}).get('tx') == sample['flow']['tx']]
            if len(matches) != 1: raise ValueError('sample phase not uniquely joined')
            recall = matches[0]; phase = recall['phase']
            metrics = RESULTS/'glm_roundtrip/full_distributed02/metrics'/phase
            timing = summarize(metrics, 2)
            for path in metrics.glob('rank*.jsonl'):
                used[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
            assert sample['correct'] and recall['correct']
            for rank in timing['ranks'].values():
                event = {e['event']: e['monotonic_ns'] for e in rank['events']}
                if 'target_copy_complete' in event:
                    assert event['target_copy_complete'] <= event['weights_ready']
            row = {'source_run': name, 'source_status': data['status'], 'phase': phase,
                'policy': sample['policy'], 'fraction': sample.get('fraction'),
                'T_ready_s': timing['load_s'], 'rank_s': timing['rank_s'], 'first_token_s': sample['first_token_s'],
                'correct': True, 'metrics_directory': str(metrics.relative_to(ROOT)),
                'flow': sample['flow'], 'source_bytes': sample.get('source_bytes'),
                'dram_bytes': sample.get('dram_bytes'), 'ssd_kernel_read_bytes': sample.get('ssd_kernel_read_bytes'),
                'selected_ssd_bytes': sample.get('selected_ssd_bytes')}
            cases.append(row)
    analysis = {'development':'v0.1.23.3','experiment_id':stage,'run_id':protocol['run_id'],
        'execution_status':'completed','functional_audit':'pass','new_gpu_runs':0,'samples':cases,'source_runs':parents}
    if stage == 'e1':
        by = defaultdict(list)
        for row in cases: by[row['policy']].append(row['T_ready_s'])
        assert all(len(values)==2 for values in by.values())
        spreads = {key: {'values_s': values, 'mean_s': statistics.mean(values), 'range_s': max(values)-min(values),
            'range_over_mean_pct': 100*(max(values)-min(values))/statistics.mean(values)} for key, values in by.items()}
        analysis.update(repeat_variation=spreads, scientific_status='qualified_historical_reuse',
            limitations=['two observations per condition do not characterize tails',
                         'original 3.5s mapping anomaly remains unconfirmed', 'not an acceptance of current deployed binaries'])
    else:
        groups = defaultdict(list)
        for row in cases: groups[row['fraction']].append(row)
        pairs = []
        for fraction, rows in sorted(groups.items()):
            assert len(rows)==2 and len({r['source_run'] for r in rows})==1
            a = next(r for r in rows if r['policy']=='local_dram_ssd')
            b = next(r for r in rows if r['policy']=='cluster_dram')
            assert a['dram_bytes'] == b['dram_bytes']
            pairs.append({'fraction':fraction, 'source_run':a['source_run'], 'local_s':a['T_ready_s'],
                'cluster_s':b['T_ready_s'], 'saved_s':a['T_ready_s']-b['T_ready_s'], 'dram_bytes':a['dram_bytes']})
        analysis.update(pairs=pairs, scientific_status='supports_locality_motivation_within_historical_protocol',
            combined_provenance=aggregate['provenance'], limitations=protocol['limits'])
    (out/'analysis.json').write_text(json.dumps(analysis, indent=2))
    (out/'commands.txt').write_text(' '.join(sys.argv)+'\n')
    (out/'source_manifest.json').write_text(json.dumps({'analysis_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'inputs':used,'interpretation':'historical source/metric identity, not current-runtime certification'}, indent=2))
    print(json.dumps({k:v for k,v in analysis.items() if k not in ('samples','source_runs')}, indent=2))


if __name__ == '__main__':
    p=argparse.ArgumentParser(); p.add_argument('--protocol', type=Path, required=True); p.add_argument('--out', type=Path, required=True)
    main(p.parse_args())
