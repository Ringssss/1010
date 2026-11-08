"""Stream bounded read-only server observations only after fixed identity checks."""
from datetime import datetime, timezone
from pathlib import Path
import glob
import json
import socket
import subprocess
import time


def emit(kind, value):
    print(json.dumps(dict(kind=kind, captured_at=datetime.now(timezone.utc).isoformat(), value=value)), flush=True)


def command(argv, timeout=1.0):
    begin = time.monotonic()
    row = dict(argv=argv, timeout_s=timeout)
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        row.update(returncode=p.returncode, stdout=p.stdout[:65536], stderr=p.stderr[:4096],
                   stdout_truncated=len(p.stdout) > 65536)
    except subprocess.TimeoutExpired as exc:
        row.update(returncode=None, timed_out=True, stdout=str(exc.stdout or b'')[:65536], stderr=str(exc.stderr or b'')[:4096])
    except OSError as exc:
        row.update(returncode=None, error=repr(exc))
    row['elapsed_s'] = time.monotonic() - begin
    return row


def read(path, limit=32768):
    try:
        with open(path) as f:
            value = f.read(limit + 1)
        return dict(path=path, text=value[:limit], truncated=len(value) > limit)
    except OSError as exc:
        return dict(path=path, error=repr(exc))


identity = dict(hostname=socket.gethostname(), boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
emit('host_identity', identity)
expected = dict(hostname='m03r2n09', boot_id='a5bc9009-f911-406d-aa56-30a8d08f01f5')
if identity != expected:
    emit('identity_guard', dict(passed=False, reason='host_identity_changed', expected=expected))
    raise SystemExit(3)
inspect = command(['docker', 'inspect', '--format', '{{json .Id}}\n{{json .State.StartedAt}}', 'zjn-fluid-basic-1bce3897a2075b'], 1.5)
emit('container_identity', inspect)
expected_container = ['7e9daa477513074bae2dda469618899705ccbf21da39e99b76ec4be25e48a402', '2026-10-06T12:39:02.990558427Z']
try:
    actual_container = [json.loads(s) for s in inspect.get('stdout', '').splitlines()]
except (ValueError, TypeError):
    actual_container = []
passed = inspect.get('returncode') == 0 and actual_container == expected_container
emit('identity_guard', dict(passed=passed, expected_container=expected_container))
if not passed:
    raise SystemExit(3)
for path in ('/proc/uptime', '/proc/loadavg', '/proc/meminfo', '/proc/pressure/cpu', '/proc/pressure/memory', '/proc/pressure/io', '/proc/sys/kernel/threads-max', '/proc/sys/kernel/pid_max', '/proc/sys/vm/overcommit_memory', '/proc/sys/fs/file-nr'):
    emit('host_resource', read(path))
# Print only explicit safe configuration keywords, retaining context and line numbers.
whitelist = {'maxstartups', 'persourcemaxstartups', 'persourcenetblocksize', 'persourcepenalties', 'logingracetime', 'maxsessions', 'maxauthtries', 'loglevel', 'usedns', 'usepam', 'port', 'listenaddress', 'include', 'match'}
for path in ['/etc/ssh/sshd_config'] + sorted(glob.glob('/etc/ssh/sshd_config.d/*.conf'))[:32]:
    row = read(path, 65536)
    text = row.pop('text', '')
    row['whitelisted_lines'] = [dict(line=i, text=line.strip()) for i, line in enumerate(text.splitlines(), 1)
                                if line.strip() and line.strip().split()[0].lower() in whitelist]
    emit('sshd_config', row)
for argv in (['ps', '-C', 'sshd', '-o', 'pid,ppid,user,stat,etimes,comm,args', '--no-headers'],
             ['ss', '-H', '-tanp', '( sport = :22 )'],
             ['journalctl', '--no-pager', '-n', '80', '--since', '-30min', '-u', 'ssh', '-u', 'sshd'],
             ['journalctl', '--no-pager', '-k', '-n', '80', '--since', '-30min']):
    emit('server_command', command(argv, 1.0))
for port in (33000, 33001):
    emit('keeper_inventory', command(['curl', '--noproxy', '*', '--connect-timeout', '1', '--max-time', '1', '-sS', f'http://12.11.3.29:{port}/inventory?compact=1'], 1.2))
emit('completed', dict(remote_signals=0, gpu_operations=0))
