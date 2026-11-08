"""Create a fixed cached-mode parameter for one fresh bounded session."""
import argparse
import json
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--path', type=Path, required=True)
    a = p.parse_args()
    path = a.path.resolve()
    if path.parent.name != 'bounded_cost' or not path.name.startswith('bounded_cost_runtime_') or not path.name.endswith('.inventory_mode.json'):
        raise ValueError('fresh bounded task mode path required')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        stream.write(json.dumps({'mode': 'cached'}) + '\n')
    print(json.dumps({'path': str(path), 'mode': 'cached', 'GPU_launches': 0}))

if __name__ == '__main__':
    main()
