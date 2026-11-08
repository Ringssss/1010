"""Check packed IPC offsets, scalar FP8 flags and routing aliases without a GPU."""
from types import SimpleNamespace
import torch

from fluidckpt.serving.ipc_layout import bind_imported


def test_packed_buffers_bind_exact_offsets_and_nested_references():
    model = torch.nn.Module()
    model.weight = torch.nn.Parameter(torch.empty((2, 2), device='meta'))
    model.bias = torch.nn.Parameter(torch.empty(4, device='meta'))
    model.routing = SimpleNamespace(correction_bias=model.bias)
    packed = torch.arange(12, dtype=torch.float32).view(torch.uint8)
    entries = {'weight': {'packed_offset_bytes': 16}, 'bias': {'packed_offset_bytes': 32}}
    rows = [{'name': 'weight', 'kind': 'param', 'storage_name': 'weight', 'dtype': 'torch.float32',
             'shape': [2, 2], 'stride': [2, 1], 'storage_offset': 0, 'attributes': {'format_ue8m0': True}},
            {'name': 'bias', 'kind': 'param', 'storage_name': 'bias', 'dtype': 'torch.float32',
             'shape': [4], 'stride': [1], 'storage_offset': 0}]
    bind_imported(model, rows, entries, {'weight': packed, 'bias': packed})
    assert torch.equal(model.weight, torch.arange(4, 8, dtype=torch.float32).reshape(2, 2))
    assert torch.equal(model.bias, torch.arange(8, 12, dtype=torch.float32))
    assert model.routing.correction_bias is model.bias
    assert model.weight.format_ue8m0
    assert model.weight.untyped_storage().data_ptr() == packed.untyped_storage().data_ptr()
    model.bias.data[0] = 27
    assert packed.view(torch.float32)[8].item() == 27
