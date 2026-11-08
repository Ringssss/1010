"""Inspect allocator handle framing without opening GPU handles."""
import argparse
import json
from pathlib import Path
import pickle

p = argparse.ArgumentParser()
p.add_argument("path", type=Path)
a = p.parse_args()
with a.path.open("rb") as stream:
    value = pickle.load(stream)
print(json.dumps({"entries": [{"name": row["name"], "nbytes": row["nbytes"],
    "handle_fields": len(row["handle"]), "device": row["handle"][0],
    "handle_bytes": len(row["handle"][1]), "handle_prefix_hex": bytes(row["handle"][1])[:8].hex(),
    "storage_size_bytes": row["handle"][2], "allocation_offset_bytes": row["handle"][3]}
    for row in value["entries"]]}, indent=2))
