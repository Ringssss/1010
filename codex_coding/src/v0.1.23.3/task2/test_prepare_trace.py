"""Diagnostics retain stage exceptions and unregister process-global GC hooks."""
import gc
import json
import pickle
import pytest
from fluidckpt.serving.prepare_trace import PrepareTrace


def test_trace_preserves_exceptions_and_records_nested_stages(tmp_path):
    callbacks = list(gc.callbacks)
    trace = PrepareTrace(tmp_path, 0, 'test')
    try:
        with pytest.raises(ValueError, match='original'):
            with trace.span('outer'):
                with trace.span('inner', entry=2):
                    gc.collect()
                    raise ValueError('original')
    finally:
        trace.finish()
    assert gc.callbacks == callbacks
    report = json.loads((tmp_path/'worker0_test.json').read_text())
    assert [r['stage'] for r in report['rows']] == ['inner', 'outer']
    assert report['rows'][0]['wall_s'] <= report['rows'][1]['wall_s']
    assert any(e['phase'] == 'stop' for e in report['gc_events'])


def test_timed_reader_does_not_change_pickle_content(tmp_path):
    value = {'entries': [('weights', bytes(range(256))*1000)], 'meta': {'tp': 0}}
    path = tmp_path/'data.pkl'; path.write_bytes(pickle.dumps(value))
    trace = PrepareTrace(tmp_path, 0, 'reader')
    try:
        with open(path, 'rb') as f:
            assert pickle.load(trace.reader(f)) == value
    finally:
        trace.finish()
    report = json.loads((tmp_path/'worker0_reader.json').read_text())
    assert any(r['stage'].startswith('file_read') for r in report['rows'])
