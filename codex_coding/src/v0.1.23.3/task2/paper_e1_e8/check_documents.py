"""Check the paper experiment documentation without running any experiment."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[5]
plan = ROOT/'docx/plans/2026-10-04_v0.1.23.3_task2_paper_e1_e8_plan.md'
record = ROOT/'docx/experiments/2026-10-04_v0.1.23.3_task2_paper_e1_e8_records.md'
process = ROOT/'code_building/process_docs/v0.1-init-project/v0.1.23.3-task2_e1_e8_document_protocol_20261004.md'
errors = []
literature = ROOT/'docx/articles/2026-10-06_weight_placement_and_topology_literature.md'
baselines = ROOT/'docx/articles/2026-10-06_baselines_delta_and_research_roadmap.md'
basic_process = ROOT/'code_building/process_docs/v0.1-init-project/v0.1.23.3-task2_basic_scenarios_20261006.md'
basic_readme = ROOT/'codex_coding/src/v0.1.23.3/task2/basic_scenarios/README.md'
for path in (plan, record, process, literature, baselines, basic_process, basic_readme):
    text = path.read_text()
    if 'v0.1.23.3' not in text:
        errors.append(f'{path}: missing development number')
    for link in re.findall(r'\]\(([^)]+)\)', text):
        if '://' in link or link.startswith('#'):
            continue
        if not (path.parent/link.split('#')[0]).resolve().exists():
            errors.append(f'{path.name}: missing link {link}')
    print(f'{path.relative_to(ROOT)}: {len(text.splitlines())} lines')
for n in range(1, 9):
    if not re.search(rf'^## .*e{n}：', plan.read_text(), re.M):
        errors.append(f'plan missing e{n}')
    if not re.search(rf'^## .*e{n} 实验记录：', record.read_text(), re.M):
        errors.append(f'record missing e{n}')
    if not re.search(rf'^\| e{n} \|', record.read_text(), re.M):
        errors.append(f'e{n} missing ledger row')
assert record.name in plan.read_text()
assert plan.name in record.read_text()
for error in errors:
    print('ERROR:', error)
print('PASS' if not errors else 'FAIL')
if errors:
    raise SystemExit(1)
