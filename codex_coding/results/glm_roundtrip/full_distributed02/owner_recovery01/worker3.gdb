set pagination off
set confirm off
set print thread-events off
set scheduler-locking off
set $fluid_gil_state = (int)PyGILState_Ensure()
call (int)PyRun_SimpleString("import sys, os, json\nfrom pathlib import Path\n_fluid_worker = None\nfor _fluid_frame in sys._current_frames().values():\n    while _fluid_frame is not None:\n        if (_fluid_frame.f_code.co_name == 'main' and\n            _fluid_frame.f_code.co_filename.endswith('/cluster/worker.py') and\n            'worker' in _fluid_frame.f_locals):\n            _fluid_worker = _fluid_frame.f_locals['worker']\n            break\n        _fluid_frame = _fluid_frame.f_back\nassert _fluid_worker is not None\nassert not _fluid_worker.runs\n_fluid_dump = {'pid': os.getpid(), 'stamp': '441210415', 'regions': [], 'manifests': {}}\nfor _fluid_entry in _fluid_worker.catalog.entries.values():\n    if _fluid_entry['state'] != 'VALID' or _fluid_entry['tier'] != 'dram': continue\n    _fluid_mapping = _fluid_entry['owner'].mapping\n    _fluid_desc = {k: v for k, v in _fluid_entry.items() if k not in ('tensor', 'owner', 'meta', 'index')}\n    _fluid_dump['regions'].append({'metadata': _fluid_desc, 'fd': _fluid_mapping.fd,\n        'inode': os.fstat(_fluid_mapping.fd).st_ino, 'lease': _fluid_mapping.info['lease']})\n    _fluid_dump['manifests'][_fluid_entry['identity']] = {'meta':\n        {k: _fluid_entry['meta'].get(k) for k in ('model_identity', 'tp_size', 'tp_rank', 'tensors')},\n        'index': _fluid_entry['index']}\nPath('/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/glm_roundtrip/full_distributed02/owner_recovery01/worker3.json').write_text(json.dumps(_fluid_dump))\ndel _fluid_worker, _fluid_frame, _fluid_dump\n")
call (void)PyGILState_Release($fluid_gil_state)
detach
quit
