"""Record or check current archive fingerprints; never modify archive files."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
parser = argparse.ArgumentParser()
parser.add_argument('--snapshot', type=Path, required=True)
parser.add_argument('--check', action='store_true')
args = parser.parse_args()
if args.check:
    rows = json.loads(args.snapshot.read_text())
    changed = [r['path'] for r in rows if hashlib.sha256((ROOT/r['path']).read_bytes()).hexdigest() != r['sha256']]
    print(json.dumps({'changed': changed}))
    if changed:
        raise SystemExit(1)
else:
    if args.snapshot.exists():
        raise ValueError('snapshot already exists')
    files = [ROOT/'history-chat.txt']
    for folder in ('docx', 'code_building'):
        files.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in ('.md', '.txt'))
    args.snapshot.write_text(json.dumps([{'path': str(p.relative_to(ROOT)), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in files], indent=2))
