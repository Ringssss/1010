"""Capture the first forward after restoration without mixing profiler time into benchmark results."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import resource
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'main_lib'))
sys.path.insert(0, str(ROOT/'codex_coding/src/task2/cluster'))
from fluidckpt.cluster.control import Cluster, request
from fluidckpt.serving.handoff import wait_for_exports
from run_lifecycle import LifecycleRun


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--name', required=True)
    args = parser.parse_args()
    root = args.run_dir.resolve()
    state = json.loads((root/'session.json').read_text())
    if (root/'lifecycle_session.json').exists():
        lifecycle = json.loads((root/'lifecycle_session.json').read_text())
        model = lifecycle['models']['A']; slot = lifecycle['slots']['A']; profile = lifecycle['profile']
    else:
        model = {'sources': state['sources'], 'source_ids': state['source_ids'], 'reference': state['reference'], 'tp': 16}
        slot = dict(state['shell'], workers=list(range(16)), command=str(root/'glm.command.json'))
        profile = state['profile']
    out = root/'forward_profiles'/args.name
    out.mkdir(parents=True, exist_ok=False)
    cluster = Cluster(state['endpoints'], profile, planner_endpoint=state['endpoints'][0])
    report = {'status': 'running', 'scope': 'diagnostic eager first-forward capture; profiler durations are not benchmark TTFT',
              'model': state['args']['model'], 'parallelism': 'TP16', 'backend': 'Hygon DTK/HIP', 'runs': []}
    def profile_request(route, body):
        req = urllib.request.Request(slot['endpoint']+'/'+route, data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=180) as response: return response.read().decode()
    try:
        for index, profiled in enumerate([False, True]):
            phase = f'profile_{args.name}_{index}'
            request(slot['endpoint'], 'release_memory_occupation', {'tags': ['weights']}, timeout=300)
            Path(slot['command']).write_text(json.dumps({'tag': phase, 'mode': 'restore',
                'metrics': str(root/'metrics'/phase)}))
            started = time.monotonic()
            with ThreadPoolExecutor(1) as pool:
                future = pool.submit(request, slot['endpoint'], 'resume_memory_occupation', {'tags': ['weights']}, 1200)
                wait_for_exports(root/'handoff'/phase, ranks=model['tp'], timeout=1200)
                targets = [dict(key=f'{phase}/{rank}', id=f'{phase}/{rank}', mode='live',
                    worker=slot['workers'][rank], tier='hbm', identity=source['identity'], tp=rank,
                    off=0, n=source['n'], meta=source['meta'], index=source['index'], tag=phase,
                    metrics_directory=str(root/'metrics'/phase)) for rank, source in enumerate(model['sources'])]
                flow = cluster.transfer(cluster.plan_restore(targets, 'resource_lp', model['source_ids']),
                                        verify_before_ready=False, retain=True)
                future.result()
            if profiled:
                profile_request('start_profile', {'output_dir': str(out/'trace'), 'num_steps': 1,
                    'activities': ['CPU', 'GPU'], 'with_stack': True, 'record_shapes': True,
                    'profile_by_stage': False, 'merge_profiles': False, 'profile_prefix': args.name})
            reply = LifecycleRun.generate_stream(None, slot)
            cluster.finish(flow['tx'])
            row = {'profiled': profiled, 'phase': phase, 'reply': reply,
                   'request_to_first_token_s': reply['first_token_time']-started,
                   'correct': reply['text'] == model['reference']['text'],
                   'flow': {key: flow[key] for key in ['prepare_s', 'execute_wall_s', 'verify_and_publish_s']}}
            report['runs'].append(row)
            (out/'summary.json').write_text(json.dumps(report, indent=2)+'\n')
            if not row['correct']: raise AssertionError('profiled restore output differs from native reference')
        deadline = time.monotonic()+60
        while time.monotonic() < deadline:
            files = list((out/'trace').glob('*.json*'))
            if len(files) >= model['tp']: break
            time.sleep(.5)
        report['trace_files'] = [str(path) for path in files]
        report['status'] = 'captured' if files else 'no_trace'
        if not files: raise RuntimeError('profiler produced no trace artifact')
    except BaseException as exc:
        report.update(status='failed', error=repr(exc)); raise
    finally:
        (out/'summary.json').write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()
