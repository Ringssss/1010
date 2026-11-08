"""Validate the authorized recovery archive without starting or modifying experiments."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
FILES = [
    "history-chat.txt",
    "code_building/history_chat/v0.1-init-project/v0.1.23.2-task2_joint_planning_recovery.txt",
    "code_building/process_docs/v0.1-init-project/v0.1.23.2-task2_system_profiling_joint_placement.md",
    "docx/articles/2026-09-29_task2_joint_placement_findings.md",
    "docx/plans/2026-09-29_task2_joint_planning_control_plan.md",
    "docx/next_step.txt", "docx/00_docx_storage_and_lookup_guide.md",
    "code_building/progress_diff_summary.md", "code_building/key_files_index.md", "code_building/key_conclusion.md",
    *[f"docx/context_index/{name}.md" for name in (
        "00_document_registry", "01_stage_summary", "02_active_threads",
        "03_current_required_process_docs", "04_insight_ledger")],
    "docx/plans/2026-09-28_task2_numa_flow_loading_plan.md",
    "docx/plans/2026-09-28_task2_stage2_replica_multipath_plan.md",
    "codex_coding/src/diag/extract_task2_archive_evidence.py",
    "codex_coding/results/task2/archive_20260929/detailed_evidence.json",
    "codex_coding/results/task2/archive_20260929/detailed_evidence.md",
]


def main():
    recovery = (ROOT/FILES[0]).read_bytes()
    assert recovery == (ROOT/FILES[1]).read_bytes(), "recovery copies differ"
    text = recovery.decode()
    assert text.startswith("【\n上一轮信息：") and text.rstrip().endswith("】")
    assert "这一轮：" in text and "v0.1.23.2" in text
    paths = sorted(set(re.findall(r"/(?:home|data)/[A-Za-z0-9_./-]+", text)))
    missing = [p for p in paths if not Path(p.rstrip(".")).exists()]
    assert not missing, missing
    checks = []
    for name in FILES:
        path = ROOT/name
        body = path.read_bytes()
        assert body, name
        checks.append({"path": name, "bytes": len(body), "lines": len(body.splitlines()),
                       "sha256": hashlib.sha256(body).hexdigest()})
    placement = json.loads((ROOT/"codex_coding/results/task2/cluster/placement01/placement_analysis.json").read_text())
    bulk = json.loads((ROOT/"codex_coding/results/task2/cluster/bulk_profile01/summary.json").read_text())
    profile = json.loads((ROOT/"codex_coding/results/task2/cluster/profile01/profiling_analysis.json").read_text())
    assert placement["passed"] and placement["source_hash_mismatches"] == [] and placement["cleanup_errors"] == []
    assert abs(placement["measured"][0]["load_s"]-2.133549089) < 1e-9
    assert abs(placement["measured"][1]["load_s"]-1.812675486) < 1e-9
    assert bulk["status"] == "failed" and bulk["profile_transfer"]["verification"]["passed"]
    assert not any(p["device_trace_available"] for p in profile["profiles"])
    evidence = json.loads((ROOT/"codex_coding/results/task2/archive_20260929/detailed_evidence.json").read_text())
    for name, sha in evidence["inputs_sha256"].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == sha, f"original evidence changed: {name}"
    extractor = ROOT/"codex_coding/src/diag/extract_task2_archive_evidence.py"
    assert hashlib.sha256(extractor.read_bytes()).hexdigest() == evidence["extractor_sha256"]
    doc = (ROOT/FILES[2]).read_text()
    generated = (ROOT/"codex_coding/results/task2/archive_20260929/detailed_evidence.md").read_text()
    assert generated.rstrip() in doc, "generated appendix differs from verified evidence"
    for letter in "ABCDEFGH":
        assert f"## 附录 {letter}：" in doc, f"missing detailed appendix {letter}"
    assert sum(line.startswith("```") for line in doc.splitlines()) % 2 == 0, "unbalanced fenced blocks"
    assert len(evidence["restores"]) == 2 and len(evidence["timeline"]) >= 10
    assert abs(sum(r["saved_s"] for r in evidence["segment_differences"])-0.320873603) < 1e-9
    result = {"passed": True, "scope": "documentation, paths, recovery-copy equality and existing result assertions; no GPU runs",
              "development": "v0.1.23.2", "recovery_copies_identical": True,
              "absolute_paths_checked": len(paths), "missing_paths": missing, "files": checks,
              "original_evidence_hashes_matched": len(evidence["inputs_sha256"]),
              "detailed_appendices": "A-H", "generated_appendix_matches": True,
              "source_results": {"placement01": "passed", "profile01_gpu_trace": "incomplete",
                                 "bulk_profile01": "failed fingerprint check; diagnostic verification passed"}}
    out = ROOT/"codex_coding/results/task2/archive_20260929"
    out.mkdir(parents=True, exist_ok=True)
    (out/"check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "files"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
