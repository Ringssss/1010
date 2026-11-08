"""Validate native SGLang CPU weight backup on DTK with an isolated memory-saver build."""
import argparse
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
    parser.add_argument('--model', default='/data/node28/shared/zhujianian/Qwen3.6-35B-A3B')
    parser.add_argument('--node', type=int, default=27)
    parser.add_argument('--gpu-base', type=int, default=0)
    parser.add_argument('--port', type=int, default=32540)
    parser.add_argument('--timeout', type=int, default=1200)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--memory-saver-package', default=str(ROOT/'codex_coding/results/tms_dtk_fixed2/package'))
    args = parser.parse_args()
    out = ROOT/'codex_coding/results/native_cpu_backup'/args.run
    out.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'scope': 'native SGLang memory_saver + weights_cpu_backup; '
        'isolated DTK compatibility patch, no FluidCKPT loading/lifecycle hook', 'args': vars(args),
        'events': [], 'roundtrips': []}
    def event(name, **fields):
        report['events'].append({'time': time.time(), 'event': name, **fields})
        (out/'summary.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report['events'][-1]), flush=True)
    endpoint = f'http://12.11.3.{args.node}:{args.port}'
    def request(route, data=None, timeout=120):
        payload = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(endpoint+'/'+route, data=payload, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read()
        if not body: return None
        try: return json.loads(body)
        except json.JSONDecodeError: return body.decode()
    def generate():
        payload = {'text': 'The capital of France is', 'sampling_params': {'temperature': 0, 'max_new_tokens': 32},
                   'stream': True}
        req = urllib.request.Request(endpoint+'/generate', data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
        started = time.monotonic(); first = None; final = {}
        with urllib.request.urlopen(req, timeout=180) as response:
            for line in response:
                if not line.startswith(b'data: '): continue
                if line[6:].strip() == b'[DONE]': break
                final = json.loads(line[6:])
                if first is None and (final.get('text') or final.get('meta_info', {}).get('completion_tokens')):
                    first = time.monotonic()
        if first is None: raise AssertionError('native generation returned no token')
        return {'text': final['text'], 'first_token_time': first, 'ttft_s': first-started,
                'generation_s': time.monotonic()-started, 'meta_info': final.get('meta_info')}
    def memory():
        code = 'import pathlib,json;print(json.dumps({p.parts[-3]:int(p.read_text()) for p in pathlib.Path("/sys/class/drm").glob("card[0-9]*/device/mem_info_vram_used")}))'
        return json.loads(remote(args.node, ['python3', '-c', code]))
    spec = out/'server.process.json'
    argv = ['python3', '-m', 'sglang.launch_server', '--model-path', args.model, '--tp-size', '2',
        '--base-gpu-id', str(args.gpu_base), '--host', '0.0.0.0', '--port', str(args.port),
        '--nccl-port', str(args.port+100), '--mem-fraction-static', '.70', '--context-length', '4096',
        '--max-total-tokens', '8192', '--max-running-requests', '4', '--disable-cuda-graph',
        '--disable-custom-all-reduce', '--attention-backend', 'triton', '--page-size', '1',
        '--moe-runner-backend', 'triton', '--mamba-scheduler-strategy', 'extra_buffer',
        '--max-mamba-cache-size', '32', '--random-seed', '42', '--enable-memory-saver', '--enable-weights-cpu-backup']
    env = {'PYTHONPATH': args.memory_saver_package,
        'FLUIDCKPT_MODE': '', 'HIP_VISIBLE_DEVICES': '0,1,2,3,4,5,6,7', 'AMD_SERIALIZE_KERNEL': '3',
        'HSA_FORCE_FINE_GRAIN_PCIE': '1', 'NCCL_NET_PLUGIN': 'librccl-net-shca.so', 'NCCL_IB_HCA': 'shca_0',
        'HSA_VMEM_POOL_SIZE': '131072',
        'SGLANG_KV_LAYOUT_HCU_FA': 'false', 'SGLANG_ROCM_USE_AITER_MOE': '0',
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}
    spec.write_text(json.dumps({'command': argv, 'env': env, 'cwd': str(ROOT), 'log': str(out/'server.log')})+'\n')
    try:
        started = time.monotonic()
        event('started', process=json.loads(remote(args.node, ['python3', str(PROCESS), 'start', '--spec', str(spec)])))
        deadline = started+args.timeout
        next_progress = 0
        while time.monotonic() < deadline:
            content = (out/'server.log').read_text(errors='replace')
            if any(token in content for token in ['Scheduler hit an exception', 'KERNEL VMFault',
                                                  'CUresult error:', 'Fatal Python error:',
                                                  'Trying to free a pointer not allocated here',
                                                  'scheduler died during initialization']):
                raise RuntimeError('native CPU-backup startup failed; inspect server.log')
            try:
                request('health', timeout=2)
                break
            except Exception:
                if time.monotonic() >= next_progress:
                    event('loading', elapsed_s=time.monotonic()-started)
                    next_progress = time.monotonic()+30
                time.sleep(1)
        else: raise TimeoutError('native CPU-backup server startup timed out')
        report['startup_s'] = time.monotonic()-started
        reference = generate(); report['reference'] = reference
        for i in range(args.repeats):
            before = memory(); started = time.monotonic()
            request('flush_cache')
            request('release_memory_occupation', {'tags': ['weights']}, timeout=300)
            release_s = time.monotonic()-started
            parked = memory(); started = time.monotonic()
            request('resume_memory_occupation', {'tags': ['weights']}, timeout=300)
            resumed = time.monotonic(); reply = generate()
            row = {'repetition': i, 'release_s': release_s, 'resume_rpc_s': resumed-started,
                   'request_to_first_token_s': reply['first_token_time']-started, 'reply': reply,
                   'correct': reply['text'] == reference['text'], 'hbm_before': before, 'hbm_parked': parked}
            report['roundtrips'].append(row)
            event('restored', repetition=i, correct=row['correct'], release_s=release_s,
                  request_to_first_token_s=row['request_to_first_token_s'])
            if not row['correct']: raise AssertionError('native CPU-backup output changed after resume')
        report['status'] = 'passed'
    except BaseException as exc:
        report.update(status='failed', error=repr(exc)); event('failed', error=repr(exc)); raise
    finally:
        try: report['cleanup'] = remote(args.node, ['python3', str(PROCESS), 'stop', '--spec', str(spec)])
        except Exception as exc: report['cleanup_error'] = repr(exc)
        event('finished')


if __name__ == '__main__':
    main()
