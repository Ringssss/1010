"""Record live worker memfd mappings for CPU-owner reconstruction without copying bytes."""
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
    if Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()[19] != state['start_time']:
        raise RuntimeError('worker PID changed')
    payload = f'''import sys, os, json
from pathlib import Path
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
assert not _fluid_worker.runs
_fluid_dump = {{'pid': os.getpid(), 'stamp': {state['start_time']!r}, 'regions': [], 'manifests': {{}}}}
for _fluid_entry in _fluid_worker.catalog.entries.values():
    if _fluid_entry['state'] != 'VALID' or _fluid_entry['tier'] != 'dram': continue
    _fluid_mapping = _fluid_entry['owner'].mapping
    _fluid_desc = {{k: v for k, v in _fluid_entry.items() if k not in ('tensor', 'owner', 'meta', 'index')}}
    _fluid_dump['regions'].append({{'metadata': _fluid_desc, 'fd': _fluid_mapping.fd,
        'inode': os.fstat(_fluid_mapping.fd).st_ino, 'lease': _fluid_mapping.info['lease']}})
    _fluid_dump['manifests'][_fluid_entry['identity']] = {{'meta':
        {{k: _fluid_entry['meta'].get(k) for k in ('model_identity', 'tp_size', 'tp_rank', 'tensors')}},
        'index': _fluid_entry['index']}}
Path({str(args.out)!r}).write_text(json.dumps(_fluid_dump))
del _fluid_worker, _fluid_frame, _fluid_dump
'''
    commands = args.out.with_suffix('.gdb')
    commands.write_text('set pagination off\nset confirm off\nset print thread-events off\n'
        'set scheduler-locking off\nset $fluid_gil_state = (int)PyGILState_Ensure()\n'
        f'call (int)PyRun_SimpleString({json.dumps(payload)})\n'
        'call (void)PyGILState_Release($fluid_gil_state)\ndetach\nquit\n')
    result = subprocess.run(['gdb', '-nx', '-nh', '--batch', '-p', str(pid), '-x', str(commands)],
                            capture_output=True, text=True, timeout=45)
    args.out.with_suffix('.log').write_text(result.stdout+'\n'+result.stderr)
    if result.returncode or not args.out.exists(): raise RuntimeError('worker mapping export failed')
    data = json.loads(args.out.read_text())
    print(json.dumps({'pid': pid, 'regions': len(data['regions'])}), flush=True)


if __name__ == '__main__':
    main()
