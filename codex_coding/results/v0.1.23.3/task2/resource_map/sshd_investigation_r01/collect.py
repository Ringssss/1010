"""Attempt exactly two serialized trusted-key SSH admissions; preserve every result."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import time

ROOT = Path(__file__).resolve().parents[6]
BASE = ROOT / 'codex_coding/results/v0.1.23.3/task2/resource_map'


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')


def command(argv, timeout, input_text=None):
    begin = time.monotonic()
    row = dict(argv=argv, started_at=now(), timeout_s=timeout)
    try:
        p = subprocess.run(argv, input=input_text, capture_output=True, text=True, timeout=timeout)
        row.update(returncode=p.returncode, stdout=p.stdout, stderr=p.stderr)
    except subprocess.TimeoutExpired as exc:
        decode = lambda v: v.decode(errors='replace') if isinstance(v, bytes) else v or ''
        row.update(returncode=None, timed_out=True, stdout=decode(exc.stdout), stderr=decode(exc.stderr))
    row.update(elapsed_s=time.monotonic() - begin, finished_at=now())
    return row


def classify(row):
    stderr = row['stderr'].lower()
    if 'exceeded maxstartups' in stderr:
        return 'explicit_maxstartups_rejection'
    if row.get('returncode') == 0:
        return 'remote_collector_completed'
    if 'host key verification failed' in stderr or 'remote host identification has changed' in stderr:
        return 'hostkey_rejected'
    if 'authenticated to' in stderr:
        return 'remote_execution_timeout_or_failed'
    if 'permission denied' in stderr:
        return 'authentication_failed'
    if 'connection timed out during banner exchange' in stderr:
        return 'banner_timeout'
    if 'connection established' in stderr:
        return 'preauthentication_handshake_failed'
    return 'connection_or_configuration_failed'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.parent != BASE or not out.name.startswith('sshd_investigation_'):
        parser.error('require fresh versioned resource_map/sshd_investigation_* result directory')
    if socket.gethostname() != 'm03r2n07':
        parser.error('authorized source is node27 only')
    out.mkdir(exist_ok=False)
    source_dir = Path(__file__).resolve().parent
    for name in ('collect.py', 'server_readonly.py'):
        (out / name).write_bytes((source_dir / name).read_bytes())
    known_paths = [Path.home() / '.ssh/known_hosts', Path.home() / '.ssh/known_hosts2']
    known_before = {str(p): sha(p) if p.exists() else None for p in known_paths}
    trusted_keys = set()
    lookups = []
    for p in known_paths:
        if not p.exists():
            continue
        row = command(['ssh-keygen', '-F', 'node29', '-f', str(p)], 2)
        lookups.append(row)
        for line in row['stdout'].splitlines():
            fields = line.split()
            if line and not line.startswith('#') and len(fields) >= 3 and fields[1] == 'ssh-ed25519':
                trusted_keys.add(tuple(fields[1:3]))
    if len(trusted_keys) != 1:
        save(out / 'trust_failed.json', dict(lookups=lookups, trusted_key_count=len(trusted_keys)))
        raise SystemExit('require exactly one previously trusted node29 ED25519 key')
    key = next(iter(trusted_keys))
    kh = out / 'known_hosts.trusted_node29'
    kh.write_text('node29 ' + ' '.join(key) + '\n')
    kh.chmod(0o400)
    save(out / 'hostkey_provenance.json', dict(lookups=lookups, trusted_key=list(key), strict_alias='node29', copied_known_hosts_sha256=sha(kh)))
    summary = dict(started_at=now(), source_host=socket.gethostname(), source_boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(), observations=[], known_hosts_before=known_before, remote_signals=0, gpu_operations=0)
    save(out / 'summary.json', summary)
    source = (source_dir / 'server_readonly.py').read_text()
    for ip in ('11.211.3.29', '12.11.3.29'):
        cfg = command(['ssh', '-G', '-o', 'Hostname=' + ip, 'node29'], 2)
        settings = dict(line.split(' ', 1) for line in cfg['stdout'].splitlines() if ' ' in line)
        save(out / ('effective_' + ip.replace('.', '_') + '.json'), {k: settings.get(k) for k in ('hostname', 'user', 'port', 'proxycommand', 'proxyjump')})
        if settings.get('hostname') != ip or settings.get('port') != '22' or any(settings.get(k) not in (None, 'none') for k in ('proxycommand', 'proxyjump')):
            raise ValueError('unexpected SSH target/proxy')
        argv = ['ssh', '-T', '-vv', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6', '-o', 'ConnectionAttempts=1', '-o', 'StrictHostKeyChecking=yes', '-o', 'UpdateHostKeys=no', '-o', 'HostKeyAlias=node29', '-o', 'UserKnownHostsFile=' + str(kh), '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'HostKeyAlgorithms=ssh-ed25519', '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-o', 'ServerAliveInterval=2', '-o', 'ServerAliveCountMax=1', '-o', 'Hostname=' + ip, 'node29', 'python3 -']
        row = dict(target=ip, route=command(['ip', '-j', 'route', 'get', ip], 2), remote_source_sha256=sha(source_dir / 'server_readonly.py'))
        row.update(command(argv, 15, source))
        row['status'] = classify(row)
        save(out / ('ssh_' + ip.replace('.', '_') + '.json'), row)
        summary['observations'].append(row)
        save(out / 'summary.json', summary)
        # A successful session supplies all needed evidence; avoid another connection.
        if row['returncode'] == 0 or 'Authenticated to ' in row['stderr']:
            break
    summary.update(finished_at=now(), known_hosts_after={str(p): sha(p) if p.exists() else None for p in known_paths})
    summary['known_hosts_unchanged'] = summary['known_hosts_before'] == summary['known_hosts_after']
    save(out / 'summary.json', summary)
    save(out / 'manifest.json', dict(created_at=now(), files={p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file()}))
    print(json.dumps(dict(out=str(out), observations=[dict(target=r['target'], status=r['status'], elapsed_s=r['elapsed_s']) for r in summary['observations']], known_hosts_unchanged=summary['known_hosts_unchanged'])))


if __name__ == '__main__':
    main()
