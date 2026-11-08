"""Reset only communication state in a live keeper, retaining owned checkpoint allocations."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid-state', type=Path, required=True)
    parser.add_argument('--master', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    state = json.loads(args.pid_state.read_text())
    pid = state['pid']
    proc = Path('/proc')/str(pid)
    if proc.joinpath('stat').read_text().split(') ', 1)[1].split()[19] != state['start_time']:
        raise RuntimeError('keeper PID was reused')
    proof = args.out.with_suffix('.json')
    patch = f'''import sys, os, json
from pathlib import Path
import torch.distributed as _fluid_dist
_fluid_worker = None
for _fluid_frame in sys._current_frames().values():
    while _fluid_frame is not None:
        if (_fluid_frame.f_code.co_name == 'main' and
            _fluid_frame.f_code.co_filename.endswith('/cluster/worker.py') and
            'worker' in _fluid_frame.f_locals):
            _fluid_worker = _fluid_frame.f_locals['worker']
            break
        _fluid_frame = _fluid_frame.f_back
assert _fluid_worker is not None
assert not _fluid_worker.runs, 'active transactions must be completed or aborted first'
_fluid_before = _fluid_worker.catalog.used()
if _fluid_dist.is_initialized():
    _fluid_dist.distributed_c10d._abort_process_group()
_fluid_worker.pair_of = {{}}
_fluid_worker.dist_ready = False
_fluid_worker.a.master = {args.master!r}
os.environ['TORCH_NCCL_ASYNC_ERROR_HANDLING'] = '0'
os.environ['TORCH_NCCL_ENABLE_MONITORING'] = '0'
assert _fluid_worker.catalog.used() == _fluid_before
Path({str(proof)!r}).write_text(json.dumps({{'used_bytes': _fluid_before, 'master': {args.master!r}}}))
del _fluid_worker, _fluid_frame
'''
    args.out.parent.mkdir(parents=True, exist_ok=True)
    commands = args.out.with_suffix('.gdb')
    commands.write_text('set pagination off\nset confirm off\nset print thread-events off\n'
        'set scheduler-locking off\nset $fluid_gil_state = (int)PyGILState_Ensure()\n'
        f'call (int)PyRun_SimpleString({json.dumps(patch)})\n'
        'call (void)PyGILState_Release($fluid_gil_state)\ndetach\nquit\n')
    result = subprocess.run(['gdb', '-nx', '-nh', '--batch', '-p', str(pid), '-x', str(commands)],
                            capture_output=True, text=True, timeout=45)
    args.out.write_text(result.stdout+'\n'+result.stderr)
    if result.returncode or not proof.exists():
        raise RuntimeError(f'group reset failed; inspect {args.out}')
    print(proof.read_text(), flush=True)


if __name__ == '__main__':
    main()
