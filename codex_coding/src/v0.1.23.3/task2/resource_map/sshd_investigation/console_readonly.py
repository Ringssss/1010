"""Collect node29 through an existing console/session; never opens SSH connections."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import glob
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

SAFE = {'maxstartups', 'persourcemaxstartups', 'persourcenetblocksize', 'persourcepenalties', 'logingracetime', 'maxsessions', 'maxauthtries', 'loglevel', 'usedns', 'usepam', 'port', 'listenaddress', 'include', 'match'}
EXPECTED = dict(hostname='m03r2n09', boot_id='a5bc9009-f911-406d-aa56-30a8d08f01f5', container_id='7e9daa477513074bae2dda469618899705ccbf21da39e99b76ec4be25e48a402', container_started_at='2026-10-06T12:39:02.990558427Z')
BASE = Path(__file__).resolve().parents[6] / 'codex_coding/results/v0.1.23.3/task2/resource_map'


def now():
    return datetime.now(timezone.utc).isoformat()


def whitelist(text):
    return [dict(line=i, text=s.strip()) for i, s in enumerate(text.splitlines(), 1)
            if s.strip() and s.strip().split()[0].lower() in SAFE]


def count_evidence(sample, count):
    if sample.get('skipped'):
        status = 'skipped'
    elif sample.get('timed_out'):
        status = 'timed_out'
    elif sample.get('returncode') != 0:
        status = 'failed_or_unavailable'
    elif sample.get('stdout_truncated'):
        status = 'truncated'
    else:
        status = 'complete'
    complete = status == 'complete'
    return dict(collection_status=status, returncode=sample.get('returncode'),
                complete=complete, count=count if complete else None,
                partial_count=count if not complete and sample.get('stdout') else None)


def socket_summary(text, sample=None):
    rows = [line.split() for line in text.splitlines() if line.strip()]
    rows = [r for r in rows if len(r) >= 5]
    evidence = count_evidence(sample if sample is not None else dict(returncode=0, stdout=text), len(rows))
    states = dict(Counter(r[0] for r in rows))
    endpoints = dict(Counter(r[4] for r in rows))
    addresses = dict(Counter(r[4].rsplit(':', 1)[0].strip('[]') for r in rows))
    return dict(socket_count=evidence.pop('count'), **evidence,
                states=states if evidence['complete'] else None,
                peer_endpoints=endpoints if evidence['complete'] else None,
                peer_addresses=addresses if evidence['complete'] else None,
                partial_states=states if not evidence['complete'] and rows else None,
                partial_peer_endpoints=endpoints if not evidence['complete'] and rows else None,
                partial_peer_addresses=addresses if not evidence['complete'] and rows else None,
                unauthenticated_connection_count=None,
                note='TCP sockets include authenticated sessions; do not equate them with MaxStartups counters.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--self-check', action='store_true', help='synthetic CPU-only helper checks; no server commands')
    args = parser.parse_args()
    if args.self_check:
        assert len(whitelist('# MaxStartups 9\nMaxStartups 10:30:100\nHostKey /secret\nMatch User x\nLoginGraceTime 60')) == 3
        x = socket_summary('ESTAB 0 0 12.11.3.29:22 12.11.3.27:42000\nLISTEN 0 128 0.0.0.0:22 0.0.0.0:*')
        assert x['socket_count'] == 2 and x['states'] == {'ESTAB': 1, 'LISTEN': 1} and x['unauthenticated_connection_count'] is None
        assert 'HostKey' not in SAFE and 'setenv' not in SAFE
        for failed in (dict(returncode=1, stdout=''), dict(returncode=None, stdout='', timed_out=True), dict(stdout='', skipped='deadline')):
            empty = socket_summary('', failed)
            assert empty['socket_count'] is None and empty['partial_count'] is None and empty['states'] is None
            assert count_evidence(failed, 0)['count'] is None
        partial = socket_summary('ESTAB 0 0 12.11.3.29:22 12.11.3.27:42000', dict(returncode=0, stdout='partial', stdout_truncated=True))
        assert partial['socket_count'] is None and partial['partial_count'] == 1 and partial['collection_status'] == 'truncated'
        print(json.dumps(dict(synthetic_checks=5, passed=True, server_operations=0, captured_at=now(), argv=sys.argv,
                              source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())))
        return
    if args.out is None:
        parser.error('--out is required unless --self-check')
    if socket.gethostname() != EXPECTED['hostname']:
        parser.error('must run from node29 existing session/console; no node27 server sampling')
    out = args.out.resolve()
    if out.parent != BASE or not out.name.startswith('sshd_investigation_console_'):
        parser.error('require fresh versioned sshd_investigation_console_* result directory')
    out.mkdir(exist_ok=False)
    (out / 'console_readonly.py').write_bytes(Path(__file__).read_bytes())
    deadline = time.monotonic() + 25
    sequence = []

    def record(kind, row):
        row = dict(kind=kind, captured_at=now(), **row)
        name = f'{len(sequence):02d}_{kind}.json'
        (out / name).write_text(json.dumps(row, ensure_ascii=False, indent=2) + '\n')
        sequence.append(name)
        return row

    def read(path, limit=32768):
        try:
            with open(path) as stream:
                text = stream.read(limit + 1)
            return dict(path=str(path), text=text[:limit], truncated=len(text) > limit)
        except OSError as exc:
            return dict(path=str(path), error=repr(exc))

    def command(argv, timeout=2):
        begin = time.monotonic()
        row = dict(argv=argv, started_at=now(), timeout_s=timeout)
        remaining = deadline - begin
        if remaining <= 0:
            return dict(**row, skipped='collector_deadline_reached')
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=min(timeout, remaining))
            row.update(returncode=p.returncode, stdout=p.stdout[:65536], stderr=p.stderr[:4096], stdout_truncated=len(p.stdout) > 65536)
        except subprocess.TimeoutExpired as exc:
            decode = lambda v: v.decode(errors='replace') if isinstance(v, bytes) else v or ''
            row.update(returncode=None, timed_out=True, stdout=decode(exc.stdout)[:65536], stderr=decode(exc.stderr)[:4096])
        except OSError as exc:
            row.update(returncode=None, error=repr(exc))
        row.update(elapsed_s=time.monotonic() - begin, finished_at=now())
        return row

    record('collector_invocation', dict(argv=sys.argv, total_command_deadline_s=25, command_stdout_max_bytes=65536, source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
    identity = dict(hostname=socket.gethostname(), boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(), uid=os.getuid(), euid=os.geteuid())
    record('host_identity', identity)
    inspect = record('container_identity', command(['docker', 'inspect', '--format', '{{json .Id}}\n{{json .State.StartedAt}}', 'zjn-fluid-basic-1bce3897a2075b']))
    try:
        parts = [json.loads(s) for s in inspect.get('stdout', '').splitlines()]
    except ValueError:
        parts = []
    continuity = identity['boot_id'] == EXPECTED['boot_id'] and parts == [EXPECTED['container_id'], EXPECTED['container_started_at']]
    record('identity_guard', dict(passed=continuity, expected=EXPECTED, old_pid_reads=0, remote_signals=0))
    # General host diagnostics do not target stale PIDs or require container continuity.
    for path in ('/proc/uptime', '/proc/loadavg', '/proc/meminfo', '/proc/pressure/cpu', '/proc/pressure/memory', '/proc/pressure/io', '/proc/sys/kernel/threads-max', '/proc/sys/kernel/pid_max', '/proc/sys/vm/overcommit_memory', '/proc/sys/fs/file-nr'):
        record('host_resource', read(path))
    record('server_package_version', command(['dpkg-query', '-W', '-f=${binary:Package} ${Version}\n', 'openssh-server']))
    ps = record('sshd_processes', command(['ps', '-C', 'sshd,sshd-session,sshd-auth', '-o', 'pid,ppid,user,stat,etimes,comm', '--no-headers']))
    lines = ps.get('stdout', '').splitlines()
    process_count = count_evidence(ps, len(lines))
    record('sshd_process_count', dict(visible_processes=process_count.pop('count'), **process_count,
                                     visibility_complete=None, note='Collection completeness does not prove root-equivalent process visibility.'))
    for line in lines[:64]:
        fields = line.split()
        if not fields or not fields[0].isdigit():
            continue
        pid = fields[0]
        safe_argv = []
        try:
            tokens = Path(f'/proc/{pid}/cmdline').read_bytes()[:8192].decode(errors='replace').split('\0')
            for i, token in enumerate(tokens[:-1]):
                if token in ('-f', '-p', '-g'):
                    safe_argv.extend([token, tokens[i + 1]])
                if token == '-o' and tokens[i + 1].split('=', 1)[0].lower() in SAFE:
                    safe_argv.extend([token, tokens[i + 1]])
            record('sshd_safe_argv', dict(pid=int(pid), options=safe_argv))
        except OSError as exc:
            record('sshd_safe_argv', dict(pid=int(pid), error=repr(exc)))
        for leaf in ('limits', 'status', 'cgroup'):
            row = read(f'/proc/{pid}/{leaf}')
            record('sshd_' + leaf, row)
            if leaf == 'cgroup' and row.get('text', '').startswith('0::'):
                cg = row['text'].splitlines()[0][3:].lstrip('/')
                for metric in ('pids.current', 'pids.max', 'pids.events', 'memory.current', 'memory.max', 'memory.events'):
                    record('sshd_cgroup_resource', read(Path('/sys/fs/cgroup') / cg / metric))
    sockets = record('ssh22_sockets', command(['ss', '-H', '-tan', '( sport = :22 )']))
    record('ssh22_counts_sources', socket_summary(sockets.get('stdout', ''), sockets))
    for path in ['/etc/ssh/sshd_config'] + sorted(glob.glob('/etc/ssh/sshd_config.d/*.conf'))[:32]:
        row = read(path, 65536)
        row['whitelisted_lines'] = whitelist(row.pop('text', ''))
        record('sshd_config_whitelist', row)
    record('effective_config_boundary', dict(verified=False, reason='live listener effective state is not inferred from static files; sshd -T can load private host keys and is not executed', include_policy='only fixed /etc/ssh/sshd_config and up to 32 /etc/ssh/sshd_config.d/*.conf; other Include targets retained as text, not followed'))
    for argv in (['journalctl', '--no-pager', '-n', '80', '--since', '-30min', '-u', 'ssh', '-u', 'sshd'], ['journalctl', '--no-pager', '-k', '-n', '80', '--since', '-30min']):
        record('limited_log', command(argv))
    if continuity:
        for port in (33000, 33001):
            record('keeper_inventory', command(['curl', '--noproxy', '*', '--connect-timeout', '2', '--max-time', '2', '-sS', f'http://12.11.3.29:{port}/inventory?compact=1'], 2.2))
    else:
        record('keeper_skipped', dict(reason='original container identity unverified or changed'))
    record('summary', dict(finished_at=now(), identity_continuity=continuity, remote_signals=0, gpu_operations=0, effective_config_verified=False,
                          permissions='No sudo; permission denial or partial process/log visibility remains evidence, never converted to zero load/connections. No environment, private keys, passwords or unrelated ports read.'))
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()}
    (out / 'manifest.json').write_text(json.dumps(dict(files=files, completed_at=now()), indent=2) + '\n')
    print(json.dumps(dict(out=str(out), identity_continuity=continuity, evidence_files=len(sequence))))


if __name__ == '__main__':
    main()
