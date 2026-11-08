"""Write a new overlay with complete recovered-target ownership; perform no runtime action."""
import argparse
import hashlib
import json
from pathlib import Path

from full_protocol import sha_file

p = argparse.ArgumentParser()
p.add_argument("--overlay", type=Path, required=True)
p.add_argument("--parent-session", type=Path, required=True)
p.add_argument("--out", type=Path, required=True)
a = p.parse_args()
if a.out.exists():
    raise FileExistsError("owned overlay must be a new file")
overlay = json.loads(a.overlay.read_text())
parent = json.loads(a.parent_session.read_text())
recovery = json.loads((a.overlay.parent / "summary.json").read_text())
if recovery["status"] != "passed" or overlay["slots"]["target_b"].get("parked") is not True:
    raise ValueError("ownership registration requires a passed parked recovery")
new_spec = str(Path(overlay["slots"]["target_b"]["process_spec"]).resolve())
old_specs = [str(Path(path).resolve()) for node, path in parent["basic_owned_processes"]
             if node == "node29" and Path(path).name == "fresh_engine_node29.process.json"]
if len(old_specs) != 1 or new_spec == old_specs[0]:
    raise ValueError("original and recovered target ownership are not unique")
old_spec = old_specs[0]
for field in ("basic_owned_processes", "new_engine_processes"):
    original = overlay.get(field, [])
    updated = []
    replaced = 0
    for node, path in original:
        if node == "node29" and str(Path(path).resolve()) == old_spec:
            updated.append([node, new_spec])
            replaced += 1
        else:
            updated.append([node, path])
    if replaced > 1:
        raise ValueError("duplicate old target engine ownership")
    if replaced == 0:
        if field == "basic_owned_processes":
            raise ValueError("original basic ownership omitted its target engine")
        updated.append(["node29", new_spec])
    if sum(node == "node29" and str(Path(path).resolve()) == new_spec for node, path in updated) != 1:
        raise ValueError("new target ownership must be registered exactly once")
    untouched_before = [(node, path) for node, path in original if not (node == "node29" and str(Path(path).resolve()) == old_spec)]
    untouched_after = [(node, path) for node, path in updated if not (node == "node29" and str(Path(path).resolve()) == new_spec)]
    if untouched_before != untouched_after:
        raise ValueError("ownership registration changed a keeper or the source engine")
    overlay[field] = updated
overlay["recovery_owned_processes"] = [["node29", new_spec]]
overlay["recovery_parent_session"] = str(a.parent_session.resolve())
overlay["recovery_parent_session_sha256"] = sha_file(a.parent_session)
overlay["ownership_registration"] = {"original_overlay": str(a.overlay.resolve()),
    "original_overlay_sha256": sha_file(a.overlay), "recovery_summary_sha256": sha_file(a.overlay.parent / "summary.json"),
    "replaced_old_target_spec": old_spec, "new_target_spec": new_spec,
    "source_engine_and_all_keeper_records_unchanged": True,
    "scope": "new overlay control data only; no cleanup, process signal or GPU action"}
a.out.write_text(json.dumps(overlay, indent=2))
print(json.dumps({"status": "passed", "owned_overlay": str(a.out), "recovery_owned_processes": overlay["recovery_owned_processes"]}))
