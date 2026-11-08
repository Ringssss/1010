#!/usr/bin/env python3
"""Verify frozen static-review evidence without overwriting original results."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result-dir', type=Path, default=Path(
        'codex_coding/results/v0.1.23.3/task2/janus_reuse/implementation_r01'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[6]
    result = args.result_dir if args.result_dir.is_absolute() else root / args.result_dir
    report = result / 'report.md'
    frozen = json.loads((result / 'evidence_refs.json').read_text())
    refs = {}
    for target, line_text in re.findall(r'\]\((/[^)]+?):(\d+)\)', report.read_text()):
        path = Path(target)
        line = int(line_text)
        data = path.read_bytes()
        lines = data.decode().splitlines()
        assert 1 <= line <= len(lines), (path, line)
        ref = {'path': str(path.relative_to(root)), 'line': line,
               'line_text': lines[line - 1], 'sha256': hashlib.sha256(data).hexdigest()}
        refs[(ref['path'], line)] = ref
    head = subprocess.check_output(['git', '-C', str(root / 'lib_cite/Janus'),
                                    'rev-parse', 'HEAD'], text=True).strip()
    status = subprocess.check_output(['git', '-C', str(root / 'lib_cite/Janus'),
                                      'status', '--porcelain=v1'], text=True)
    lock = json.loads((root / 'lib_cite/baselines.lock.json').read_text())
    expected = next(repo['commit'] for repo in lock['repositories'] if repo['name'] == 'Janus')
    assert head == expected == frozen['baseline_head']
    assert not status
    assert hashlib.sha256(report.read_bytes()).hexdigest() == frozen['report_sha256']
    assert list(refs.values()) == frozen['unique_line_refs']
    print(json.dumps({'status': 'frozen_refs_verified', 'baseline_head': head,
                      'baseline_clean': True, 'unique_line_refs': len(refs),
                      'unique_source_files': len({ref['path'] for ref in refs.values()}),
                      'outputs_written': 0, 'runtime_or_backend_validated': False},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
