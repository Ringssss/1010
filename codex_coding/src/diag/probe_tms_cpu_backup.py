"""Check native memory-saver CPU backup, physical HBM release and exact contents on DTK."""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import sys
import time


def worker(args):
    import torch
    from torch_memory_saver import torch_memory_saver as saver
    torch.cuda.set_device(args.gpu)
    if args.mode == 'torch':
        saver.hook_mode = 'torch'
    count = args.mib << 20
    expected = torch.arange(count, dtype=torch.int64).remainder(251).to(torch.uint8)
    with saver.region(tag='weights', enable_cpu_backup=True):
        tensor = expected.to(f'cuda:{args.gpu}')
    torch.cuda.synchronize()
    torch.cuda.empty_cache()
    address = tensor.data_ptr()
    free_loaded = torch.cuda.mem_get_info()[0]
    started = time.monotonic()
    saver.pause('weights')
    torch.cuda.synchronize()
    pause_s = time.monotonic()-started
    free_parked = torch.cuda.mem_get_info()[0]
    if free_parked-free_loaded < count:
        raise AssertionError(f'physical HBM did not decrease by the payload size: {free_parked-free_loaded}')
    started = time.monotonic()
    saver.resume('weights')
    torch.cuda.synchronize()
    resume_s = time.monotonic()-started
    recovered = tensor.cpu()
    if tensor.data_ptr() != address or not torch.equal(recovered, expected):
        raise AssertionError('native CPU backup changed the address or the tensor contents')
    result = {'status': 'passed', 'backend': 'torch_memory_saver HIP/DTK', 'hook_mode': args.mode,
        'scope': 'synthetic byte tensor; not GLM or an SGLang server comparison',
        'torch': torch.__version__, 'hip': torch.version.hip, 'nbytes': count,
        'physical_hbm_released_bytes': free_parked-free_loaded, 'pause_s': pause_s,
        'resume_s': resume_s, 'address_preserved': True,
        'sha256': hashlib.sha256(recovered.numpy().tobytes()).hexdigest()}
    result['repeated_cycles'] = []
    for cycle in range(args.extra_cycles):
        value = (31+cycle*7) % 251
        tensor.fill_(value)
        torch.cuda.synchronize()
        saver.pause('weights')
        replacement = torch.full((count,), 13, dtype=torch.uint8, device=f'cuda:{args.gpu}')
        saver.resume('weights')
        torch.cuda.synchronize()
        cpu_correct = bool(torch.all(tensor.cpu() == value))
        gpu_correct = bool(torch.all(tensor == value).item())
        result['repeated_cycles'].append({'cycle': cycle, 'cpu_copy_correct': cpu_correct,
                                         'gpu_compute_correct': gpu_correct})
        (args.out/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        if not cpu_correct or not gpu_correct:
            result['status'] = 'failed'
            (args.out/'result.json').write_text(json.dumps(result, indent=2)+'\n')
            raise AssertionError('repeated remapping changed device or DMA-visible bytes')
        del replacement
    (args.out/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result), flush=True)


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--gpu', type=int, default=2)
    parser.add_argument('--mib', type=int, default=64)
    parser.add_argument('--mode', choices=['torch', 'preload'], default='preload')
    parser.add_argument('--extra-cycles', type=int, default=0)
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out/'source_sha256.txt').write_text(hashlib.sha256(Path(__file__).read_bytes()).hexdigest()+'\n')
    from torch_memory_saver import configure_subprocess
    from contextlib import nullcontext
    command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--out', str(args.out),
               '--gpu', str(args.gpu), '--mib', str(args.mib), '--mode', args.mode,
               '--extra-cycles', str(args.extra_cycles)]
    with (configure_subprocess() if args.mode == 'preload' else nullcontext()):
        with (args.out/'worker.log').open('w') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=90)
    if result.returncode:
        existing = args.out/'result.json'
        report = json.loads(existing.read_text()) if existing.exists() else {}
        report.update(status='failed', returncode=result.returncode)
        existing.write_text(json.dumps(report, indent=2)+'\n')
        raise SystemExit(result.returncode)
    print((args.out/'result.json').read_text())


if __name__ == '__main__':
    main()
