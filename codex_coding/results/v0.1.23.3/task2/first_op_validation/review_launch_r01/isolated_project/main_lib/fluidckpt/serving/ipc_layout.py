"""Bind logical tensor views inside already imported, possibly packed IPC buffers."""
from .layout import bind


def bind_imported(model, tensors, entries, buffers):
    import torch
    rows = []
    for tensor in tensors:
        name = tensor['storage_name']
        dtype = getattr(torch, tensor['dtype'].split('.')[-1])
        element_size = torch.empty((), dtype=dtype).element_size()
        offset = entries[name].get('packed_offset_bytes', 0)
        if offset % element_size:
            raise ValueError(f'IPC storage {name} is not aligned for {dtype}')
        rows.append(dict(tensor, storage_offset=tensor['storage_offset']+offset//element_size))
    # bind also preserves FP8 flags and references held in non-Module routing configs.
    bind(model, {'tensors': rows}, buffers)
