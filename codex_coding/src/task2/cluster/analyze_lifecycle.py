"""Summarize real lifecycle measurements without converting failures to speedups."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    data = json.loads((args.run_dir/'summary.json').read_text())
    transitions = data.get('transitions', [])
    output = {'status': data['status'], 'kind': 'analysis_of_existing_lifecycle_run', 'transitions': [],
              'scope': 'Hygon DCU; adapted HIP IPC and warm disk fallback; eager mode; one trace, not a P99 study'}
    for row in transitions:
        output['transitions'].append({k: row[k] for k in ['phase', 'model', 'node', 'mode',
            'first_correct_token_s', 'switch_to_first_token_s', 'release_s', 'correct', 'routes'] if k in row})
    def latest(prefix):
        return next((r for r in reversed(transitions) if r['phase'].startswith(prefix)), None)
    pairs = [('baseline_A_to_B_disk', 'fluid_A_to_B', 'switch_to_first_token_s'),
             ('baseline_B_to_A_disk', 'fluid_B_to_A', 'switch_to_first_token_s'),
             ('baseline_remote_A_disk', 'fluid_remote_A', 'first_correct_token_s')]
    output['paired_observations'] = []
    for base, candidate, key in pairs:
        a, b = latest(base), latest(candidate)
        if a and b and a['correct'] and b['correct']:
            output['paired_observations'].append({'baseline': a['phase'], 'candidate': b['phase'],
                'metric': key, 'baseline_s': a[key], 'candidate_s': b[key], 'ratio': a[key]/b[key]})
    samples = data.get('B_while_A_moves', [])
    idle = data.get('B_idle_samples', [])
    output['B_samples'] = {'idle_count': len(idle), 'during_movement_count': len(samples),
                           'errors': [s['error'] for s in samples if 'error' in s]}
    for label, rows in [('idle', idle), ('during_movement', samples)]:
        good = [r for r in rows if 'ttft_s' in r]
        if good:
            output['B_samples'][label] = {'median_ttft_s': statistics.median(r['ttft_s'] for r in good),
                'max_ttft_s': max(r['ttft_s'] for r in good),
                'median_generation_s': statistics.median(r['generation_s'] for r in good)}
    session = args.run_dir/'session.json'
    if session.exists():
        state = json.loads(session.read_text())
        reference = state['runtime']['models']['B']['reference']['text']
        output['B_samples']['all_outputs_match'] = bool(samples) and all(s.get('text') == reference for s in samples)
    placement = data.get('eviction_plan')
    if placement:
        output['placement'] = {k: placement[k] for k in ['solve_s', 'optimal', 'mip_gap', 'predicted_mean_s', 'scope']}
        tiers = {}
        for e in placement['extents']:
            location = placement['locations'][e['location']]
            key = location['node']+':'+e['tier']
            tiers[key] = tiers.get(key, 0)+e['n']
        output['placement']['bytes_by_location'] = tiers
        output['placement']['total_weight_bytes'] = sum(tiers.values())
    migration = data.get('migration', {})
    output['migration'] = {k: migration[k] for k in ['prepare_s', 'execute_wall_s', 'verify_and_publish_s',
                            'coordinator_wall_s', 'verification'] if k in migration}
    if 'verification' in output['migration']:
        output['migration']['verification'] = {k: migration['verification'][k] for k in ['passed', 'checked_bytes', 'algorithm']}
    output['ssd'] = data.get('ssd_statistics')
    output['service_reserve_bytes_per_gpu'] = data.get('service_reserve_bytes_per_gpu')
    root = Path(__file__).resolve().parents[4]
    output['changed_source_files_since_start'] = [p for p, sha in data.get('code_sha256', {}).items()
        if (root/p).exists() and hashlib.sha256((root/p).read_bytes()).hexdigest() != sha]
    output['cleanup_errors'] = data.get('cleanup_errors', [])
    output['passed'] = (data['status'] == 'passed' and all(r['correct'] for r in transitions)
                         and not output['B_samples']['errors'] and not output['cleanup_errors'])
    if placement:
        output['passed'] = output['passed'] and output['B_samples'].get('all_outputs_match', False)
    (args.run_dir/'analysis.json').write_text(json.dumps(output, indent=2, ensure_ascii=False)+'\n')
    print(json.dumps({k: output[k] for k in ['status', 'passed', 'paired_observations', 'B_samples', 'placement'] if k in output}, indent=2))


if __name__ == '__main__': main()
