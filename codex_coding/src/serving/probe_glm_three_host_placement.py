"""Test four SGLang TP launch groups placed on three physical DCU hosts."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import resource
import time
import urllib.request

from run_glm_native import ROOT, PROCESS, remote


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--reference', required=True)
    parser.add_argument('--timeout', type=int, default=1200)
    parser.add_argument('--ib-hca', default='')
    args = parser.parse_args()
    groups = [(29, 0), (29, 4), (28, 2), (27, 4)]
    out = ROOT/'codex_coding/results/glm_three_node'/args.run
    out.mkdir(parents=True, exist_ok=False)
    specs = []
    report = {'status': 'running', 'scope': 'TP16 in four launch groups on three physical hosts',
              'model': args.model, 'groups': groups, 'events': []}
    report['ib_hca'] = args.ib_hca or 'automatic'
    def event(name, **fields):
        row = {'time': time.time(), 'event': name, **fields}
        report['events'].append(row)
        (out/'summary.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(row), flush=True)
    def query(operation, payload=None, timeout=3):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request('http://12.11.3.29:32453/'+operation, data=data,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content = response.read()
        return json.loads(content) if content else None
    started = time.monotonic()
    try:
        for rank in [3, 2, 1, 0]:
            node, base_gpu = groups[rank]
            spec = out/f'group{rank}_node{node}.process.json'
            argv = ['python3', '-m', 'sglang.launch_server', '--model-path', args.model,
                '--tp-size', '16', '--nnodes', '4', '--node-rank', str(rank), '--base-gpu-id', str(base_gpu),
                '--dist-init-addr', '12.11.3.29:26553', '--host', '0.0.0.0', '--port', str(32453+rank),
                '--trust-remote-code', '--context-length', '16384', '--max-total-tokens', '32768',
                '--max-running-requests', '16', '--chunked-prefill-size', '4096',
                '--mem-fraction-static', '.85', '--disable-cuda-graph', '--disable-custom-all-reduce',
                '--moe-runner-backend', 'triton', '--fp8-gemm-backend', 'triton',
                '--kv-cache-dtype', 'bf16', '--watchdog-timeout', '3600', '--random-seed', '42']
            env = {'HIP_VISIBLE_DEVICES': '0,1,2,3,4,5,6,7', 'AMD_SERIALIZE_KERNEL': '0',
                   'NCCL_NET_PLUGIN': 'librccl-net-shca.so', 'NCCL_SOCKET_IFNAME': 'ib0',
                   'GLOO_SOCKET_IFNAME': 'ib0', 'NCCL_DEBUG': 'WARN', 'HSA_FORCE_FINE_GRAIN_PCIE': '1',
                   'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'SGLANG_ROCM_USE_AITER_MOE': '0',
                   'FLUIDCKPT_MODE': '', 'PYTHONPATH': ''}
            if args.ib_hca:
                env['NCCL_IB_HCA'] = args.ib_hca
            spec.write_text(json.dumps({'command': argv, 'env': env, 'cwd': str(ROOT),
                                        'log': str(out/f'group{rank}.log')}, indent=2)+'\n')
            specs.append((node, spec))
            process = remote(node, ['python3', str(PROCESS), 'start', '--spec', str(spec)])
            event('started', group=rank, node=node, base_gpu=base_gpu, process=json.loads(process))
        deadline = started+args.timeout
        next_progress = 0
        while time.monotonic() < deadline:
            for log in out.glob('group*.log'):
                content = log.read_text(errors='replace')
                if any(text in content for text in ['Scheduler hit an exception', 'KERNEL VMFault',
                                                    'Fatal Python error:', 'Address already in use']):
                    raise RuntimeError(f'placement failed: {log}')
            try:
                query('health')
                break
            except Exception:
                if time.monotonic() >= next_progress:
                    event('loading', elapsed_s=time.monotonic()-started)
                    next_progress = time.monotonic()+30
                time.sleep(1)
        else:
            raise TimeoutError('placement startup deadline exceeded')
        report['generations'] = []
        reference = json.loads(Path(args.reference).read_text())
        for original in reference['generations']:
            reply = query('generate', {'text': original['prompt'],
                'sampling_params': {'temperature': 0, 'max_new_tokens': 32}}, timeout=180)
            report['generations'].append({'prompt': original['prompt'], 'reply': reply,
                'matches_original_placement': reply['text'] == original['answers'][0]['text']})
            event('generated', text=reply['text'])
        report['status'] = 'generated'
        report['all_outputs_match'] = all(row['matches_original_placement'] for row in report['generations'])
    except BaseException as exc:
        report.update(status='failed', error=repr(exc))
        event('failed', error=repr(exc))
    finally:
        def stop(item):
            node, spec = item
            try:
                return {'node': node, 'result': remote(node, ['python3', str(PROCESS), 'stop', '--spec', str(spec)])}
            except Exception as exc:
                return {'node': node, 'error': repr(exc)}
        with ThreadPoolExecutor(4) as pool:
            report['cleanup'] = list(pool.map(stop, specs))
        event('finished', elapsed_s=time.monotonic()-started)
    if report['status'] != 'generated':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
