"""Standalone standard-library CLI for exclusively creating this task's mode file."""
import argparse
import json
from pathlib import Path
def create(path):
    path=Path(path).resolve()
    if path.parent.name!='cost_ledger' or not path.name.startswith('cost_ledger_runtime_') or not path.name.endswith('.inventory_mode.json'):
        raise ValueError('new cost ledger scoped mode path required')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as stream:stream.write('{"mode":"cached"}\n')
    return dict(path=str(path),mode='cached',GPU_launches=0)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--path',required=True)
    print(json.dumps(create(p.parse_args().path)))
