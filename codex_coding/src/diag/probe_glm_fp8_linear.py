"""Compare vendor FP8 GEMM backends with a CPU reference at GLM projection sizes."""
import argparse
import json
from pathlib import Path
import resource
from types import SimpleNamespace

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['hipblaslt', 'triton'], required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    import torch
    from sglang.srt.server_args import set_global_server_args_for_scheduler
    from sglang.srt.layers.quantization.fp8_utils import (
        hipblaslt_w8a8_block_fp8_linear, triton_w8a8_block_fp8_linear)
    set_global_server_args_for_scheduler(SimpleNamespace(
        enable_deterministic_inference=False, enable_fused_moe_sum_all_reduce=False,
        disaggregation_mode='null'))
    torch.set_num_threads(8)
    torch.manual_seed(42)
    torch.cuda.set_device(0)
    fn = hipblaslt_w8a8_block_fp8_linear if args.backend == 'hipblaslt' else triton_w8a8_block_fp8_linear
    report = {'backend': args.backend, 'cases': []}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for m, n, k in [(6, 2624, 6144), (6, 4096, 2048), (6, 128, 6144), (1, 2624, 6144)]:
        x = torch.randn(m, k, dtype=torch.bfloat16)
        w = (torch.randn(n, k)*.02).to(torch.float8_e4m3fn)
        reference = x.float() @ w.float().T
        weight = w.cuda()
        scale = torch.ones((n+127)//128, (k+127)//128, device='cuda')
        if args.backend == 'hipblaslt':
            weight = weight.T.contiguous()
            scale = scale.T.contiguous()
        x = x.cuda()
        torch.cuda.synchronize()
        print(json.dumps({'stage': 'before_gemm', 'backend': args.backend, 'm': m, 'n': n, 'k': k}), flush=True)
        y = fn(x, weight, [128, 128], scale)
        torch.cuda.synchronize()
        output = y.float().cpu()
        relative_l2 = float(torch.linalg.vector_norm(output-reference)/torch.linalg.vector_norm(reference))
        row = {'m': m, 'n': n, 'k': k, 'relative_l2': relative_l2,
               'finite': bool(torch.isfinite(output).all())}
        report['cases'].append(row)
        args.out.write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(row), flush=True)
        if not row['finite'] or relative_l2 > .1:
            raise AssertionError('FP8 GEMM differs substantially from CPU reference')


if __name__ == '__main__':
    main()
