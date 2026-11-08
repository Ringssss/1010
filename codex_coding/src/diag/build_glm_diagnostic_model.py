"""Copy a few real GLM layers for fast backend diagnostics; this is not the full model."""
import argparse
import json
from pathlib import Path
import re
import resource
import time

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('/data/node28/shared/zhujianian/GLM-5.3'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--layers', type=int, default=8)
    args = parser.parse_args()
    import torch
    from safetensors import safe_open
    from safetensors.torch import save_file
    torch.set_num_threads(4)
    args.out.mkdir(parents=True, exist_ok=False)
    config = json.loads((args.source/'config.json').read_text())
    config['num_hidden_layers'] = args.layers
    config['num_nextn_predict_layers'] = 0
    for key in ['indexer_types', 'mlp_layer_types']:
        if key in config:
            config[key] = config[key][:args.layers]
    (args.out/'config.json').write_text(json.dumps(config, indent=2)+'\n')
    for path in args.source.iterdir():
        if path.name not in ('config.json', 'model.safetensors.index.json') and path.suffix in (
                '.json', '.model', '.txt', '.py', '.jinja', '.tiktoken'):
            (args.out/path.name).symlink_to(path)
    original = json.loads((args.source/'model.safetensors.index.json').read_text())
    selected = {}
    for key, filename in original['weight_map'].items():
        match = re.match(r'model\.layers\.(\d+)\.', key)
        if (match and int(match.group(1)) < args.layers) or key.startswith(('model.embed_tokens.', 'lm_head.', 'model.norm.')):
            selected.setdefault(filename, []).append(key)
    index = {'metadata': {'total_size': 0}, 'weight_map': {}}
    pending, pending_bytes, shard = {}, 0, 0
    started = time.monotonic()

    def flush():
        nonlocal pending, pending_bytes, shard
        if not pending:
            return
        name = f'diagnostic-{shard:04d}.safetensors'
        save_file(pending, str(args.out/name), metadata={'format': 'pt'})
        index['weight_map'].update({key: name for key in pending})
        index['metadata']['total_size'] += pending_bytes
        print(json.dumps({'shard': shard, 'bytes': pending_bytes, 'elapsed_s': time.monotonic()-started}), flush=True)
        shard += 1
        pending, pending_bytes = {}, 0

    for filename, keys in sorted(selected.items()):
        with safe_open(args.source/filename, framework='pt', device='cpu') as reader:
            for key in keys:
                value = reader.get_tensor(key)
                n = value.numel()*value.element_size()
                if pending_bytes+n > 1 << 30:
                    flush()
                pending[key] = value
                pending_bytes += n
        flush()
    (args.out/'model.safetensors.index.json').write_text(json.dumps(index)+'\n')
    (args.out/'diagnostic_manifest.json').write_text(json.dumps({
        'source': str(args.source), 'layers': args.layers, 'bytes': index['metadata']['total_size'],
        'purpose': 'real-weight reduced-depth backend diagnostic; no full-GLM accuracy/performance claims',
        'elapsed_s': time.monotonic()-started}, indent=2)+'\n')


if __name__ == '__main__':
    main()
