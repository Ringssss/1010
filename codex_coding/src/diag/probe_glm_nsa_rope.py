"""Isolate the vendor NSA normalization/RoPE call on synthetic GLM-shaped tensors."""
import argparse
import json
from pathlib import Path
import resource

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    import torch
    from lightop import attention
    torch.manual_seed(42)
    torch.cuda.set_device(0)
    report = {'device': str(torch.cuda.get_device_properties(0)), 'torch': torch.__version__,
              'operation_doc': attention.fuse_layernorm_rotary_embedding.__doc__, 'cases': []}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for tokens in [1, 6, 32, 128]:
        q = torch.randn(tokens, 32, 128, device='cuda', dtype=torch.bfloat16)
        k = torch.randn(tokens, 1, 128, device='cuda', dtype=torch.bfloat16)
        weight = torch.randn(128, device='cuda', dtype=torch.float32)
        bias = torch.randn(128, device='cuda', dtype=torch.float32)
        positions = torch.arange(tokens, device='cuda', dtype=torch.int64)
        inv_freq = 1.0 / (8000000.0 ** (torch.arange(0, 64, 2, device='cuda').float()/64))
        freq = torch.outer(torch.arange(256, device='cuda').float(), inv_freq)
        cache = torch.cat((freq.cos(), freq.sin()), dim=-1).to(torch.bfloat16)
        q_ref = q.float().clone()
        k_ref = torch.nn.functional.layer_norm(k.float(), (128,), weight, bias, 1e-6)
        cos = cache[positions, :32].float().unsqueeze(1)
        sin = cache[positions, 32:].float().unsqueeze(1)
        for value in [q_ref, k_ref]:
            even = value[..., :64:2].clone()
            odd = value[..., 1:64:2].clone()
            value[..., :64:2] = even*cos-odd*sin
            value[..., 1:64:2] = odd*cos+even*sin
        torch.cuda.synchronize()
        print(json.dumps({'stage': 'before_vendor_call', 'tokens': tokens,
                          'q_shape': list(q.shape), 'k_shape': list(k.shape),
                          'cache_shape': list(cache.shape)}), flush=True)
        attention.fuse_layernorm_rotary_embedding(positions, q, k, 128, cache, False,
                                                  None, None, weight, bias, None, None, 1e-6)
        torch.cuda.synchronize()
        row = {'tokens': tokens, 'q_max_abs': (q.float()-q_ref).abs().max().item(),
               'k_max_abs': (k.float()-k_ref).abs().max().item(),
               'finite': bool(torch.isfinite(q).all() and torch.isfinite(k).all())}
        report['cases'].append(row)
        args.out.write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(row), flush=True)


if __name__ == '__main__':
    main()
