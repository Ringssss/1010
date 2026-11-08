"""Validate exact IPC offset planning without claiming GPU correctness."""
from fluidckpt.benchmarks.serverless_adapter import destination_plan, raw_ipc_handle

index = {"a": [0, 16], "b": [16, 32], "c": [48, 64]}
export = {"meta": {"bytes": 112}, "entries": [
    {"name": "a", "nbytes": 16, "handle": (0, b"\x02c"+b"s"*64, 16, 512, b"ref", 0, b"ev", False)},
    {"name": "b", "nbytes": 32, "handle": (0, b"\x02c"+b"s"*64, 32, 1024, b"ref", 1, b"ev", False)},
    {"name": "c", "nbytes": 64, "handle": (0, b"o"*64, 64, 2048, b"ref", 2, b"ev", False)}]}
plan = destination_plan(index, export)
assert plan["handles"] == [b"s"*64, b"o"*64]
assert plan["chunks"] == [(0, 16, 512, 0), (16, 32, 1024, 0), (48, 64, 2048, 1)]
assert plan["logical_bytes"] == 112
assert plan["ipc_handle_formats"] == {"pytorch_version2_malloc_66": 2, "legacy_raw64": 1}
for bad_handle in (b"\x02e"+b"x"*64, b"\x03c"+b"x"*64, b"x"*65, b"x"*67):
    try:
        raw_ipc_handle(bad_handle)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown allocator framing was accepted")
for bad in ({"a": [0, 16], "b": [17, 32], "c": [49, 64]},
            {"a": [0, 15], "b": [15, 32], "c": [47, 64]}):
    try:
        destination_plan(bad, export)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid coverage or IPC nbytes was accepted")
print("PASS: observed malloc66 framing, raw64, unknown frames rejected, offsets/dedup/coverage/nbytes (CPU contracts only)")
