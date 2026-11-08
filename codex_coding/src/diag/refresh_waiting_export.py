"""Refresh IPC export tokens from a recorded model still waiting in its capture hook."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rank', type=int, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    found = subprocess.check_output(['pgrep', '-f', f'^sglang::scheduler_TP{args.rank}([ ]|$)'], text=True).split()
    if len(found) != 1:
        raise RuntimeError(f'expected one waiting rank, found {found}')
    pid = int(found[0])
    proof = args.out.with_suffix('.json')
    patch = f'''import sys, json
from pathlib import Path
from fluidckpt.serving.sglang_hook import export_storages
_fluid_frame = None
for _fluid_top in sys._current_frames().values():
    while _fluid_top is not None:
        if (_fluid_top.f_code.co_name == 'load_model' and
            _fluid_top.f_code.co_filename.endswith('/serving/sglang_hook.py') and
            _fluid_top.f_locals.get('tp') == {args.rank} and 'model' in _fluid_top.f_locals):
            _fluid_frame = _fluid_top
            break
        _fluid_top = _fluid_top.f_back
assert _fluid_frame is not None, 'capture loader frame was not found'
_fluid_info = export_storages(_fluid_frame.f_locals['model'], {args.rank},
                             _fluid_frame.f_locals['device_config'].gpu_id, 'capture')
Path({str(proof)!r}).write_text(json.dumps(_fluid_info))
del _fluid_frame, _fluid_top
'''
    commands = args.out.with_suffix('.gdb')
    commands.write_text('set pagination off\nset confirm off\nset print thread-events off\n'
        'set scheduler-locking off\n'
        'set $fluid_gil_state = (int)PyGILState_Ensure()\n'
        f'call (int)PyRun_SimpleString({json.dumps(patch)})\n'
        'call (void)PyGILState_Release($fluid_gil_state)\ndetach\nquit\n')
    result = subprocess.run(['gdb', '-nx', '-nh', '--batch', '-p', str(pid), '-x', str(commands)],
                             capture_output=True, text=True, timeout=45)
    args.out.write_text(result.stdout+'\n'+result.stderr)
    if result.returncode or not proof.exists():
        raise RuntimeError(f'export refresh failed; inspect {args.out}')
    print(json.dumps({'pid': pid, 'rank': args.rank, 'bytes': json.loads(proof.read_text())['bytes']}), flush=True)


if __name__ == '__main__':
    main()
