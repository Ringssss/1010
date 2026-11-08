"""Record source identity and resolve report line anchors without loading GPUs."""
import hashlib
import json
import pathlib
import re
import subprocess

root = pathlib.Path('/home/zhujianian/zhujianian/fluidckpt_zjn')
out = root / 'codex_coding/results/v0.1.23.3/task2/janus_reuse/implementation_r01'
report = out / 'report.md'
refs = []
for target, line_text in re.findall(r'\]\((/[^)]+?):(\d+)\)', report.read_text()):
    path = pathlib.Path(target)
    line = int(line_text)
    data = path.read_bytes()
    lines = data.decode().splitlines()
    assert 1 <= line <= len(lines), (path, line)
    refs.append({'path': str(path.relative_to(root)), 'line': line,
                 'line_text': lines[line - 1], 'sha256': hashlib.sha256(data).hexdigest()})
head = subprocess.check_output(['git', '-C', str(root / 'lib_cite/Janus'), 'rev-parse', 'HEAD'], text=True).strip()
status = subprocess.check_output(['git', '-C', str(root / 'lib_cite/Janus'), 'status', '--porcelain=v1'], text=True)
lock = json.loads((root / 'lib_cite/baselines.lock.json').read_text())
expected = next(repo['commit'] for repo in lock['repositories'] if repo['name'] == 'Janus')
assert head == expected == 'ec1beca2dbef5628b38e266fdc9afa0dba451678'
assert not status
unique = {(ref['path'], ref['line']): ref for ref in refs}
evidence = {'scope': 'local_static_source_only', 'baseline_head': head,
            'baseline_clean': True, 'report_sha256': hashlib.sha256(report.read_bytes()).hexdigest(),
            'unique_line_refs': list(unique.values())}
(out / 'evidence_refs.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
qualification = {'status': 'static_evidence_refs_passed', 'gpu_samples': 0,
                 'baseline_head_matches_lock': True, 'baseline_clean': True,
                 'report_line_refs': len(refs), 'unique_line_refs': len(unique),
                 'unique_source_files': len({ref['path'] for ref in refs}),
                 'runtime_or_backend_validated': False,
                 'commands': ['python3 codex_coding/results/v0.1.23.3/task2/janus_reuse/implementation_r01/audit_refs.py']}
(out / 'qualification.json').write_text(json.dumps(qualification, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(qualification, ensure_ascii=False))
