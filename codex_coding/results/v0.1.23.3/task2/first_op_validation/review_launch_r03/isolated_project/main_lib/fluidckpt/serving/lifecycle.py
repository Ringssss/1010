"""Eager SGLang weight release/resume while retaining the initialized engine."""
import gc
import json
import os
from pathlib import Path
import time


def install():
    if not os.environ.get('FLUIDCKPT_LIFECYCLE'):
        return
    import torch
    from sglang.srt.managers.scheduler_update_weights_mixin import SchedulerUpdateWeightsMixin
    from sglang.srt.managers.io_struct import (ReleaseMemoryOccupationReqOutput,
                                             ResumeMemoryOccupationReqOutput)
    from fluidckpt.serving.layout import materialize
    from fluidckpt.serving.sglang_hook import export_storages, _wait_done
    from fluidckpt.serving.measure import emit

    def release(self, req):
        if not self.is_fully_idle():
            raise RuntimeError('weight release requires drained requests')
        runner = self.tp_worker.model_runner
        if not self.server_args.disable_cuda_graph:
            raise RuntimeError('this lifecycle adapter requires eager execution')
        torch.cuda.synchronize()
        emit(runner.tp_rank, 'release_begin', allocated_bytes=torch.cuda.memory_allocated())
        self.flush_cache()
        if not hasattr(runner, '_fluid_layout'):
            source = Path(os.environ['FLUIDCKPT_MANIFEST']).parent
            runner._fluid_layout = json.loads((source / f'layout.rank{runner.tp_rank}.json').read_text())
        self.offload_tags.add('weights')
        if hasattr(runner.model, '_ipc_imported_tensors'):
            runner.model._ipc_imported_tensors.clear()
        materialize(runner.model, runner._fluid_layout, 'meta')
        gc.collect()
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()
        torch.cuda.synchronize()
        torch.distributed.barrier(group=self.tp_cpu_group)
        emit(runner.tp_rank, 'release_end', allocated_bytes=torch.cuda.memory_allocated(),
             free_bytes=torch.cuda.mem_get_info()[0])
        return ReleaseMemoryOccupationReqOutput()

    def resume(self, req):
        runner = self.tp_worker.model_runner
        command = json.loads(Path(os.environ['FLUIDCKPT_LIFECYCLE']).read_text())
        os.environ['FLUIDCKPT_TAG'] = command['tag']
        os.environ['FLUIDCKPT_METRICS'] = command['metrics']
        mode = command.get('mode', 'restore')
        emit(runner.tp_rank, 'load_begin', resume_mode=mode)
        if mode == 'disk':
            import copy
            from sglang.srt.model_loader import loader as L
            from sglang.srt.configs.load_config import LoadFormat
            from sglang.srt.configs.device_config import DeviceConfig
            original = L.DefaultModelLoader._fluidckpt_original_load_model
            config = copy.copy(runner.load_config)
            config.load_format = LoadFormat.AUTO
            loader = L.DefaultModelLoader(config)
            # The factory caches module objects, including buffers parked on meta.
            # A fresh disk-loaded model must construct its own rotary buffers.
            from sglang.srt.layers.rotary_embedding import factory
            factory._ROPE_DICT.clear()
            runner.model = original(loader, model_config=runner.model_config,
                                    device_config=DeviceConfig(runner.device, runner.gpu_id))
        elif mode == 'ipc':
            from fluidckpt.serving.ipc_attach import attach
            attach(runner.model, str(Path(command['ipc_directory']) / f'rank{runner.tp_rank}.pkl'), runner.gpu_id)
        elif mode == 'restore':
            materialize(runner.model, runner._fluid_layout, f'cuda:{runner.gpu_id}')
            emit(runner.tp_rank, 'model_layout_end')
            export_storages(runner.model, runner.tp_rank, runner.gpu_id, 'restore')
            _wait_done(runner.tp_rank, float(os.environ.get('FLUIDCKPT_TIMEOUT', '600')))
        else:
            raise ValueError(f'unknown resume mode: {mode}')
        torch.cuda.synchronize()
        torch.distributed.barrier(group=self.tp_cpu_group)
        self.offload_tags.discard('weights')
        emit(runner.tp_rank, 'weights_ready', allocated_bytes=torch.cuda.memory_allocated())
        return ResumeMemoryOccupationReqOutput()

    SchedulerUpdateWeightsMixin.release_memory_occupation = release
    SchedulerUpdateWeightsMixin.resume_memory_occupation = resume
