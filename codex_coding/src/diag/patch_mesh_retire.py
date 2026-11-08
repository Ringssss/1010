"""Apply the recorded compact-reply keeper fix across this experiment's live mesh."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    args = parser.parse_args()
    state = json.loads(args.session.read_text())
    output = args.session.parent/'capture_recovery'
    reports = []
    for node, spec in state['processes']:
        spec = Path(spec)
        if not spec.name.startswith('worker_'):
            continue
        label = spec.name.removesuffix('.process.json')
        container = state['nodes'][node]['container']
        log = output/f'{label}_compact_patch.log'
        command = ['docker', 'exec', '--privileged', container, 'python3',
                   str(ROOT/'codex_coding/src/diag/patch_keeper_retire.py'),
                   '--pid-state', str(spec.with_suffix('.pid.json')), '--out', str(log)]
        if node != 'node27':
            command = ['ssh', '-o', 'BatchMode=yes', state['nodes'][node]['ip'], shlex.join(command)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=45)
        row = {'node': node, 'worker': label, 'returncode': result.returncode,
               'stdout': result.stdout, 'stderr': result.stderr}
        reports.append(row)
        (output/'mesh_patch.json').write_text(json.dumps(reports, indent=2)+'\n')
        print(json.dumps(row), flush=True)
        if result.returncode:
            raise RuntimeError('keeper patch failed; inspect the recorded log')


if __name__ == '__main__':
    main()
