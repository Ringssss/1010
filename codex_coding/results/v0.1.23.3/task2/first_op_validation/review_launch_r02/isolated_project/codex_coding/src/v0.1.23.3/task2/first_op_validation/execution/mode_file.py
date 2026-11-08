"""Standalone stdlib-only, task-scoped, exclusive cached-mode file creation."""
import argparse
import json
from pathlib import Path

def create(path):
    path = Path(path).resolve()
    if path.parent.name != 'first_op_validation' or not path.name.startswith('first_op_runtime_') or not path.name.endswith('.inventory_mode.json'):
        raise ValueError('new first-operation task mode path required')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as stream:
        stream.write(json.dumps({'mode':'cached'})+'\n')
    return dict(path=str(path),mode='cached',GPU_launches=0)
if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--path',type=Path,required=True)
    print(json.dumps(create(parser.parse_args().path)))
