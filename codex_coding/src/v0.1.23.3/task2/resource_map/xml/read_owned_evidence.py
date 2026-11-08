"""Reuse frozen read-only collection with a versioned owned-run path guard."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "codex_coding/src/v0.1.23.3/task2/topology_alignment/probe"))
import read_evidence as previous


def owned_directory(path):
    path = Path(path).resolve()
    allowed = ROOT / "codex_coding/results/v0.1.23.3/task2/resource_map"
    if path.parent != allowed or not path.name.startswith("xml_startup_"):
        raise ValueError("require owned resource_map/xml_startup_* run")
    return path


if __name__ == "__main__":
    previous.owned_directory = owned_directory
    previous.main()
