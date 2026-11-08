"""Preserve explicit prior-run source identities before an authorized revision."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
RESULT_ROOT = ROOT / "codex_coding/results/v0.1.23.3/task2/resource_map"


def main(args):
    source = args.run.resolve()
    out = args.out.resolve()
    if source.parent != RESULT_ROOT or out.parent != RESULT_ROOT or not out.name.startswith("local_xml_source_snapshot_"):
        raise ValueError("require isolated versioned source snapshot")
    if args.current_source:
        raw = json.dumps({"code_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(Path(__file__).parent.glob("*.py"))},
             "cpu_result_directory": str(source), "scope": "current revised local diagnostic/runner sources"}).encode()
    else:
        raw = (source / "summary.json").read_bytes()
    value = json.loads(raw)
    rows = []
    payloads = []
    for path, sha in value["code_sha256"].items():
        original = Path(path)
        relative = original.relative_to(ROOT)
        payload = original.read_bytes()
        if hashlib.sha256(payload).hexdigest() != sha:
            raise ValueError(f"old explicit source changed: {path}")
        payloads.append((relative, payload))
        rows.append({"source": str(original), "saved": str(out / "source_snapshot" / relative), "sha256": sha})
    out.mkdir(exist_ok=False)
    for relative, payload in payloads:
        saved = out / "source_snapshot" / relative
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(payload)
    (out / "raw_summary.json").write_bytes(raw)
    manifest = {"run": str(source), "source_summary_sha256": hashlib.sha256(raw).hexdigest(), "files": rows,
                "all_saved_sha_match": all(hashlib.sha256(Path(row["saved"]).read_bytes()).hexdigest() == row["sha256"] for row in rows),
                "scope": "explicit old code_sha256 only; added diagnostics do not change those identities"}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"out": str(out), "files": len(rows), "passed": manifest["all_saved_sha_match"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--current-source", action="store_true")
    main(parser.parse_args())
