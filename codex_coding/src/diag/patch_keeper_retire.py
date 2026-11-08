"""Apply the compact retirement reply fix to a recorded live keeper without discarding its bytes."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid-state', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    state = json.loads(args.pid_state.read_text())
    pid = state['pid']
    process = Path('/proc')/str(pid)
    stamp = (process/'stat').read_text().split(') ', 1)[1].split()[19]
    if stamp != state['start_time'] or b'fluidckpt.cluster.worker' not in (process/'cmdline').read_bytes():
        raise RuntimeError('recorded keeper identity no longer matches')
    patch = '''import __main__
assert hasattr(__main__, 'ClusterWorker')
def _fluid_retire_compact(self, body):
    self.catalog.retire(body['id'])
    __main__.torch.cuda.empty_cache()
    return self.inventory(compact=True)
__main__.ClusterWorker.retire = _fluid_retire_compact
print('FluidCKPT live fix: compact retirement replies installed', flush=True)
'''
    args.out.parent.mkdir(parents=True, exist_ok=True)
    commands = args.out.with_suffix('.gdb')
    commands.write_text('set pagination off\nset confirm off\nset print thread-events off\n'
        'set scheduler-locking off\n'
        'set $fluid_gil_state = (int)PyGILState_Ensure()\n'
        f'call (int)PyRun_SimpleString({json.dumps(patch)})\n'
        'call (void)PyGILState_Release($fluid_gil_state)\n'
        'detach\nquit\n')
    result = subprocess.run(['gdb', '-nx', '-nh', '--batch', '-p', str(pid), '-x', str(commands)],
                             text=True, capture_output=True, timeout=30)
    args.out.write_text(result.stdout+'\n'+result.stderr)
    print(json.dumps({'pid': pid, 'returncode': result.returncode, 'log': str(args.out)}))
    if result.returncode:
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
