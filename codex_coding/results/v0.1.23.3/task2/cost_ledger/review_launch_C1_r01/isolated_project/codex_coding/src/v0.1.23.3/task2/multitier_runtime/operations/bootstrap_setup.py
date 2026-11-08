"""Receive frozen setup files into a fresh task bootstrap, then run setup."""
import base64
import json
from pathlib import Path
import subprocess
import sys


def main():
    root, node, boot = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    payload = json.loads(sys.stdin.read())
    if Path('/proc/sys/kernel/random/boot_id').read_text().strip() != boot:
        raise SystemExit('boot changed')
    if root.parent != Path('/tmp') or root.name != 'fluidckpt_' + payload['spec']['run'] + '_bootstrap':
        raise SystemExit('own bootstrap path mismatch')
    root.mkdir(parents=True, exist_ok=True)
    source, spec = root / (node + '.setup_host.py'), root / (node + '.spec.json')
    with source.open('xb') as stream:
        stream.write(base64.b64decode(payload['wrapper'], validate=True))
    with spec.open('x') as stream:
        json.dump(payload['spec'], stream)
    done = subprocess.run(['python3', str(source), 'setup', str(spec)], timeout=30)
    raise SystemExit(done.returncode)


if __name__ == '__main__':
    main()
