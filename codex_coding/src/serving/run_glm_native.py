"""Run and archive a native GLM TP16 smoke test on node27 and node29."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import resource
import shlex
import signal
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
PROCESS = ROOT/'codex_coding/src/task2/cluster/process.py'


def remote(node, argv, timeout=65):
    script = 'ulimit -c 0\nsource /opt/dtk/env.sh >/dev/null 2>&1\nexec '+shlex.join(argv)
    cmd = ['docker', 'exec', f'zjn-keep-v02-n{node}', 'bash', '-lc', script]
    if node != 27:
        cmd = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', f'12.11.3.{node}', shlex.join(cmd)]
    return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT, timeout=timeout)


def query(port, route, data=None, timeout=5):
    payload = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(f'http://12.11.3.27:{port}/{route}', data=payload,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as reply:
        body = reply.read()
    return json.loads(body) if body else None


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    def interrupted(signum, frame):
        raise InterruptedError(f'controller interrupted by signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--model', default='/data/node28/shared/zhujianian/GLM-5.3')
    parser.add_argument('--port', type=int, default=32153)
    parser.add_argument('--dist-port', type=int, default=26453)
    parser.add_argument('--timeout', type=int, default=2700)
    parser.add_argument('--serialize', default='0')
    parser.add_argument('--disable-aiter-moe', action='store_true')
    parser.add_argument('--ib-hca', default='')
    parser.add_argument('--extra', nargs=argparse.REMAINDER, default=[])
    args = parser.parse_args()
    out = ROOT/'codex_coding/results/glm_three_node'/args.run
    out.mkdir(parents=True, exist_ok=False)
    report = {'scope': 'native SGLang, no FluidCKPT weight/capture hook',
              'status': 'running', 'args': vars(args), 'events': [], 'processes': {},
              'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    diagnostic_manifest = Path(args.model)/'diagnostic_manifest.json'
    if diagnostic_manifest.exists():
        report['diagnostic_model'] = json.loads(diagnostic_manifest.read_text())

    def event(name, **fields):
        report['events'].append({'time': time.time(), 'event': name, **fields})
        (out/'summary.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report['events'][-1]), flush=True)

    started = time.monotonic()
    specs = []
    try:
        env = {'HIP_VISIBLE_DEVICES': '0,1,2,3,4,5,6,7', 'AMD_SERIALIZE_KERNEL': args.serialize,
               'NCCL_NET_PLUGIN': 'librccl-net-shca.so', 'NCCL_SOCKET_IFNAME': 'ib0',
               'GLOO_SOCKET_IFNAME': 'ib0', 'NCCL_DEBUG': 'WARN',
               'HSA_FORCE_FINE_GRAIN_PCIE': '1', 'HF_HUB_OFFLINE': '1',
               'TRANSFORMERS_OFFLINE': '1', 'FLUIDCKPT_MODE': '', 'PYTHONPATH': ''}
        if args.disable_aiter_moe:
            env['SGLANG_ROCM_USE_AITER_MOE'] = '0'
        if args.ib_hca:
            env['NCCL_IB_HCA'] = args.ib_hca
        for node, rank in [(29, 1), (27, 0)]:
            argv = ['python3', '-m', 'sglang.launch_server', '--model-path', args.model,
                '--tp-size', '16', '--nnodes', '2', '--node-rank', str(rank),
                '--dist-init-addr', f'12.11.3.27:{args.dist_port}',
                '--host', '0.0.0.0', '--port', str(args.port), '--trust-remote-code',
                '--context-length', '16384', '--max-total-tokens', '32768',
                '--max-running-requests', '16', '--chunked-prefill-size', '4096',
                '--mem-fraction-static', '0.85', '--disable-cuda-graph',
                '--disable-custom-all-reduce', '--moe-runner-backend', 'triton',
                '--kv-cache-dtype', 'bf16', '--watchdog-timeout', '3600', '--random-seed', '42']
            argv.extend(args.extra)
            spec = out/f'node{node}.process.json'
            spec.write_text(json.dumps({'command': argv, 'env': env, 'cwd': str(ROOT),
                                        'log': str(out/f'node{node}.log')}, indent=2)+'\n')
            specs.append((node, spec))
            result = remote(node, ['python3', str(PROCESS), 'start', '--spec', str(spec)])
            report['processes'][str(node)] = json.loads(result)
            event('started', node=node, elapsed_s=time.monotonic()-started)
        deadline = started + args.timeout
        next_progress = 0
        while time.monotonic() < deadline:
            for node, _ in specs:
                log = out/f'node{node}.log'
                content = log.read_text(errors='replace') if log.exists() else ''
                if any(token in content for token in ['KERNEL VMFault', 'Fatal Python error:',
                                                      'Scheduler hit an exception', 'torch.OutOfMemoryError']):
                    raise RuntimeError(f'node{node}: native server failure; inspect {log}')
            try:
                query(args.port, 'health')
                break
            except Exception:
                if time.monotonic() >= next_progress:
                    event('loading', elapsed_s=time.monotonic()-started)
                    next_progress = time.monotonic()+30
                time.sleep(1)
        else:
            raise TimeoutError('native GLM health deadline exceeded')
        report['startup_s'] = time.monotonic()-started
        event('healthy', startup_s=report['startup_s'])
        report['generations'] = []
        for prompt in ['The capital of France is', 'Question: What is 2 + 3?\nAnswer:']:
            answers = []
            for repeat in range(2):
                begin = time.monotonic()
                answer = query(args.port, 'generate', {'text': prompt, 'sampling_params': {
                    'temperature': 0, 'max_new_tokens': 32}}, timeout=180)
                answers.append(answer)
                event('generated', prompt=prompt, repeat=repeat, elapsed_s=time.monotonic()-begin,
                      text=answer['text'])
            row = {'prompt': prompt, 'answers': answers, 'equal': answers[0]['text'] == answers[1]['text']}
            report['generations'].append(row)
            if not row['equal'] or not answers[0]['text']:
                raise RuntimeError('native generation was empty or not repeatable')
        report['status'] = 'generated_repeatably'
        event('generation_complete')
    except BaseException as exc:
        report['status'] = 'interrupted' if isinstance(exc, (KeyboardInterrupt, InterruptedError)) else 'failed'
        report['error'] = repr(exc)
        event('failed', error=repr(exc))
    finally:
        def stop(item):
            node, spec = item
            try:
                return {'node': node, 'result': remote(node, ['python3', str(PROCESS), 'stop', '--spec', str(spec)])}
            except Exception as exc:
                return {'node': node, 'error': repr(exc)}
        with ThreadPoolExecutor(2) as pool:
            report['cleanup'] = list(pool.map(stop, specs))
        event('finished', elapsed_s=time.monotonic()-started)
    if report['status'] != 'generated_repeatably':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
