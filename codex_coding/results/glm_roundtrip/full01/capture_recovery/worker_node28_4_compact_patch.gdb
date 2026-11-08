set pagination off
set confirm off
set print thread-events off
set scheduler-locking off
set $fluid_gil_state = (int)PyGILState_Ensure()
call (int)PyRun_SimpleString("import __main__\nassert hasattr(__main__, 'ClusterWorker')\ndef _fluid_retire_compact(self, body):\n    self.catalog.retire(body['id'])\n    __main__.torch.cuda.empty_cache()\n    return self.inventory(compact=True)\n__main__.ClusterWorker.retire = _fluid_retire_compact\nprint('FluidCKPT live fix: compact retirement replies installed', flush=True)\n")
call (void)PyGILState_Release($fluid_gil_state)
detach
quit
