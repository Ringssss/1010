"""Install the corrected prepare method into an idle recorded worker, preserving allocations."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid-state', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    state = json.loads(args.pid_state.read_text()); pid = state['pid']
    if Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()[19] != state['start_time']:
        raise RuntimeError('worker PID changed')
    proof = args.out.with_suffix('.json')
    payload = f'''import __main__, ast, sys, json, hashlib
from pathlib import Path
_fluid_worker = None
for _fluid_frame in sys._current_frames().values():
    while _fluid_frame is not None:
        if (_fluid_frame.f_code.co_name == 'main' and
            _fluid_frame.f_code.co_filename.endswith('/cluster/worker.py') and
            'worker' in _fluid_frame.f_locals):
            _fluid_worker = _fluid_frame.f_locals['worker']; break
        _fluid_frame = _fluid_frame.f_back
assert _fluid_worker is not None and not _fluid_worker.runs
_fluid_before = _fluid_worker.catalog.used()
_fluid_path = Path(__main__.__file__)
_fluid_source = _fluid_path.read_text()
_fluid_class = next(n for n in ast.parse(_fluid_source).body if isinstance(n, ast.ClassDef) and n.name == 'ClusterWorker')
_fluid_method = next(n for n in _fluid_class.body if isinstance(n, ast.FunctionDef) and n.name == 'prepare')
exec(compile(ast.Module(body=[_fluid_method], type_ignores=[]), str(_fluid_path), 'exec'), vars(__main__))
__main__.ClusterWorker.prepare = __main__.prepare
del __main__.prepare
assert _fluid_worker.catalog.used() == _fluid_before
Path({str(proof)!r}).write_text(json.dumps({{'pid': {pid}, 'used_bytes': _fluid_before,
    'source_sha256': hashlib.sha256(_fluid_source.encode()).hexdigest(), 'installed': 'prepare'}}))
del _fluid_worker, _fluid_frame, _fluid_method, _fluid_class
'''
    args.out.parent.mkdir(parents=True, exist_ok=True)
    command = args.out.with_suffix('.gdb')
    command.write_text('set pagination off\nset confirm off\nset print thread-events off\n'
        'set scheduler-locking off\nset $fluid_gil_state = (int)PyGILState_Ensure()\n'
        f'call (int)PyRun_SimpleString({json.dumps(payload)})\n'
        'call (void)PyGILState_Release($fluid_gil_state)\ndetach\nquit\n')
    result = subprocess.run(['gdb', '-nx', '-nh', '--batch', '-p', str(pid), '-x', str(command)],
                            capture_output=True, text=True, timeout=45)
    args.out.write_text(result.stdout+'\n'+result.stderr)
    if result.returncode or not proof.exists(): raise RuntimeError('prepare installation failed')
    print(proof.read_text(), flush=True)


if __name__ == '__main__':
    main()
