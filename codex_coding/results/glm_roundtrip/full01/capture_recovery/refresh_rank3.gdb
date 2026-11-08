set pagination off
set confirm off
set print thread-events off
set scheduler-locking off
set $fluid_gil_state = (int)PyGILState_Ensure()
call (int)PyRun_SimpleString("import sys, json\nfrom pathlib import Path\nfrom fluidckpt.serving.sglang_hook import export_storages\n_fluid_frame = None\nfor _fluid_top in sys._current_frames().values():\n    while _fluid_top is not None:\n        if (_fluid_top.f_code.co_name == 'load_model' and\n            _fluid_top.f_code.co_filename.endswith('/serving/sglang_hook.py') and\n            _fluid_top.f_locals.get('tp') == 3 and 'model' in _fluid_top.f_locals):\n            _fluid_frame = _fluid_top\n            break\n        _fluid_top = _fluid_top.f_back\nassert _fluid_frame is not None, 'capture loader frame was not found'\n_fluid_info = export_storages(_fluid_frame.f_locals['model'], 3,\n                             _fluid_frame.f_locals['device_config'].gpu_id, 'capture')\nPath('/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/glm_roundtrip/full01/capture_recovery/refresh_rank3.json').write_text(json.dumps(_fluid_info))\ndel _fluid_frame, _fluid_top\n")
call (void)PyGILState_Release($fluid_gil_state)
detach
quit
