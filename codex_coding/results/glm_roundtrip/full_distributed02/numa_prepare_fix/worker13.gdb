set pagination off
set confirm off
set print thread-events off
set scheduler-locking off
set $fluid_gil_state = (int)PyGILState_Ensure()
call (int)PyRun_SimpleString("import __main__, ast, sys, json, hashlib\nfrom pathlib import Path\n_fluid_worker = None\nfor _fluid_frame in sys._current_frames().values():\n    while _fluid_frame is not None:\n        if (_fluid_frame.f_code.co_name == 'main' and\n            _fluid_frame.f_code.co_filename.endswith('/cluster/worker.py') and\n            'worker' in _fluid_frame.f_locals):\n            _fluid_worker = _fluid_frame.f_locals['worker']; break\n        _fluid_frame = _fluid_frame.f_back\nassert _fluid_worker is not None and not _fluid_worker.runs\n_fluid_before = _fluid_worker.catalog.used()\n_fluid_path = Path(__main__.__file__)\n_fluid_source = _fluid_path.read_text()\n_fluid_class = next(n for n in ast.parse(_fluid_source).body if isinstance(n, ast.ClassDef) and n.name == 'ClusterWorker')\n_fluid_method = next(n for n in _fluid_class.body if isinstance(n, ast.FunctionDef) and n.name == 'prepare')\nexec(compile(ast.Module(body=[_fluid_method], type_ignores=[]), str(_fluid_path), 'exec'), vars(__main__))\n__main__.ClusterWorker.prepare = __main__.prepare\ndel __main__.prepare\nassert _fluid_worker.catalog.used() == _fluid_before\nPath('/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/glm_roundtrip/full_distributed02/numa_prepare_fix/worker13.json').write_text(json.dumps({'pid': 40987, 'used_bytes': _fluid_before,\n    'source_sha256': hashlib.sha256(_fluid_source.encode()).hexdigest(), 'installed': 'prepare'}))\ndel _fluid_worker, _fluid_frame, _fluid_method, _fluid_class\n")
call (void)PyGILState_Release($fluid_gil_state)
detach
quit
