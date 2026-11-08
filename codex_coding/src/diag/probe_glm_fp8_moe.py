"""Validate GLM TP16 expert shapes with the vendor AITER override disabled."""
import argparse
import json
import os
from pathlib import Path
import resource
from types import SimpleNamespace

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
os.environ['SGLANG_ROCM_USE_AITER_MOE'] = '0'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    import torch
    from sglang.srt.server_args import set_global_server_args_for_scheduler
    from sglang.srt.layers.moe.moe_runner.triton_utils.fused_moe import fused_experts_impl
    set_global_server_args_for_scheduler(SimpleNamespace(enable_deterministic_inference=False,
        enable_fused_moe_sum_all_reduce=False, disaggregation_mode='null'))
    torch.set_num_threads(8)
    torch.manual_seed(42)
    torch.cuda.set_device(0)
    e, k, n = 256, 6144, 128
    w1 = (torch.randn(e, 2*n, k)*.01).to(torch.float8_e4m3fn)
    w2 = (torch.randn(e, k, n)*.01).to(torch.float8_e4m3fn)
    a, b = w1.cuda(), w2.cuda()
    report = {'env': {'SGLANG_ROCM_USE_AITER_MOE': '0'}, 'cases': []}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for m in [6, 1, 32, 128]:
        x = torch.randn(m, k, dtype=torch.bfloat16)
        ids = torch.arange(8, dtype=torch.int32).repeat(m, 1)
        scores = torch.full((m, 8), 1/8, dtype=torch.float32)
        reference = torch.zeros(m, k)
        for expert in range(8):
            v = x.float() @ w1[expert].float().T
            v = torch.nn.functional.silu(v[:, :n])*v[:, n:]
            reference += v @ w2[expert].float().T/8*2.5
        torch.cuda.synchronize()
        print(json.dumps({'stage': 'before_moe', 'm': m, 'e': e, 'k': k, 'n': n}), flush=True)
        y = fused_experts_impl(x.cuda(), a, b, scores.cuda(), ids.cuda(), use_fp8_w8a8=True,
            w1_scale=torch.ones(e, 2*n//128, k//128, device='cuda'),
            w2_scale=torch.ones(e, k//128, n//128, device='cuda'),
            block_shape=[128, 128], routed_scaling_factor=2.5)
        torch.cuda.synchronize()
        result = y.float().cpu()
        error = float(torch.linalg.vector_norm(result-reference)/torch.linalg.vector_norm(reference))
        row = {'m': m, 'finite': bool(torch.isfinite(result).all()), 'relative_l2': error}
        report['cases'].append(row)
        args.out.write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(row), flush=True)
        assert row['finite'] and error < .15


if __name__ == '__main__':
    main()
