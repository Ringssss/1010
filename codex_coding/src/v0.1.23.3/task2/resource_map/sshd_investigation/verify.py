"""Verify recorded trust, bounded serialized attempts, and every frozen evidence hash."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    summary = json.loads((out / 'summary.json').read_text())
    manifest = json.loads((out / 'manifest.json').read_text())
    checks = []
    for name, expected in manifest['files'].items():
        checks.append(dict(name='frozen_sha:' + name, passed=hashlib.sha256((out / name).read_bytes()).hexdigest() == expected))
    rows = summary['observations']
    checks += [dict(name='exact_two_targets_once', passed=[r['target'] for r in rows] == ['11.211.3.29', '12.11.3.29']),
               dict(name='serialized_clients', passed=rows[0]['finished_at'] < rows[1]['started_at']),
               dict(name='known_hosts_unchanged', passed=summary['known_hosts_unchanged']),
               dict(name='no_remote_output_or_authentication', passed=all(not r['stdout'] and 'Authenticated to ' not in r['stderr'] for r in rows)),
               dict(name='exact_maxstartups_both', passed=all(r['returncode'] == 255 and 'banner line 0: Exceeded MaxStartups' in r['stderr'] for r in rows))]
    for row in rows:
        argv = row['argv']
        opts = [argv[i + 1] for i, s in enumerate(argv[:-1]) if s == '-o']
        checks.append(dict(name='strict_bounded:' + row['target'], passed=row['timeout_s'] == 15 and row['elapsed_s'] < 15 and
                           all(s in opts for s in ['StrictHostKeyChecking=yes', 'UpdateHostKeys=no', 'HostKeyAlias=node29', 'HostKeyAlgorithms=ssh-ed25519', 'ConnectionAttempts=1', 'ConnectTimeout=6', 'ControlPath=none'])))
    result = dict(passed=all(c['passed'] for c in checks), check_count=len(checks), checks=checks,
                  server_access_verified=False, remote_signals=0, gpu_operations=0)
    (out / 'validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
