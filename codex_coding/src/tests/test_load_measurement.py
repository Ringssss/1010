"""Common timing uses the complete multi-rank window and rejects incompatible clocks."""
import json
import pytest
from fluidckpt.serving.measure import summarize


def write_events(path, rank, begin, end, host="node"):
    rows = [{"event": event, "monotonic_ns": value, "host": host}
            for event, value in (("load_begin", begin), ("weights_ready", end))]
    (path / f"rank{rank}.jsonl").write_text("\n".join(json.dumps(r) for r in rows))


def test_rank_skew_is_included(tmp_path):
    write_events(tmp_path, 0, 0, 4_000_000_000)
    write_events(tmp_path, 1, 3_000_000_000, 6_000_000_000)
    result = summarize(tmp_path, 2)
    assert result["load_s"] == 6
    assert result["rank_s"] == {"0": 4, "1": 3}
    assert result["arrival_skew_s"] == 3


def test_cross_host_clock_rejected(tmp_path):
    write_events(tmp_path, 0, 0, 10, "a")
    write_events(tmp_path, 1, 0, 10, "b")
    with pytest.raises(ValueError):
        summarize(tmp_path, 2)


def test_duplicate_boundaries_rejected(tmp_path):
    write_events(tmp_path, 0, 0, 10)
    p = tmp_path / "rank0.jsonl"
    p.write_text(p.read_text() + "\n" + p.read_text())
    with pytest.raises(ValueError):
        summarize(tmp_path, 1)


def test_keeper_event_on_another_host_rejected(tmp_path):
    write_events(tmp_path, 0, 0, 10, "target")
    p = tmp_path / "rank0.jsonl"
    with p.open("a") as stream:
        stream.write("\n" + json.dumps({"event": "target_mapped", "host": "source", "monotonic_ns": 5}))
    with pytest.raises(ValueError):
        summarize(tmp_path, 1)
