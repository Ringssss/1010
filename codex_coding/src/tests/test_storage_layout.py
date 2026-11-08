"""Check alias reconstruction and allocation-free parked model views."""
import torch
from types import SimpleNamespace
from fluidckpt.serving.layout import materialize, scalar_attributes


def test_storage_alias_and_noncontiguous_view():
    model = torch.nn.Module()
    model.register_parameter('weight', torch.nn.Parameter(torch.zeros(3, 4)))
    model.transposed = model.weight.T
    layout = {'storages': [{'name': 'weight', 'nbytes': 48}], 'tensors': [
        {'name': 'weight', 'kind': 'param', 'storage_name': 'weight', 'dtype': 'torch.float32',
         'shape': [3, 4], 'stride': [4, 1], 'storage_offset': 0},
        {'name': 'transposed', 'kind': 'attr', 'storage_name': 'weight', 'dtype': 'torch.float32',
         'shape': [4, 3], 'stride': [1, 4], 'storage_offset': 0}]}
    assert materialize(model, layout, 'cpu') == 48
    model.weight.data.copy_(torch.arange(12).reshape(3, 4))
    assert torch.equal(model.transposed, model.weight.T)
    assert model.transposed.untyped_storage().data_ptr() == model.weight.untyped_storage().data_ptr()
    materialize(model, layout, 'meta')
    assert model.weight.is_meta and model.transposed.is_meta
    assert model.transposed.stride() == (1, 4)


def test_fp8_scale_flags_survive_park_and_fresh_restore():
    model = torch.nn.Module()
    scale = torch.nn.Parameter(torch.ones(2, 2), requires_grad=False)
    scale.format_ue8m0 = True
    scale.input_dim = 1
    scale.temporary_tensor = torch.ones(17)
    model.register_parameter('weight_scale_inv', scale)
    attributes = scalar_attributes(scale)
    assert 'temporary_tensor' not in attributes
    layout = {'storages': [{'name': 'weight_scale_inv', 'nbytes': 16}], 'tensors': [
        {'name': 'weight_scale_inv', 'kind': 'param', 'storage_name': 'weight_scale_inv',
         'dtype': 'torch.float32', 'shape': [2, 2], 'stride': [2, 1], 'storage_offset': 0,
         'attributes': attributes}]}
    materialize(model, layout, 'meta')
    assert model.weight_scale_inv.format_ue8m0 is True
    assert model.weight_scale_inv.input_dim == 1
    assert not hasattr(model.weight_scale_inv, 'temporary_tensor')
    fresh = torch.nn.Module()
    fresh.register_parameter('weight_scale_inv', torch.nn.Parameter(torch.empty(2, 2, device='meta')))
    materialize(fresh, layout, 'cpu')
    assert fresh.weight_scale_inv.format_ue8m0 is True
    assert fresh.weight_scale_inv.input_dim == 1


def test_routing_config_keeps_reference_to_rebound_bias():
    model = torch.nn.Module()
    model.gate = torch.nn.Module()
    model.gate.bias = torch.nn.Parameter(torch.arange(4, dtype=torch.float32))
    model.topk = torch.nn.Module()
    model.topk.config = SimpleNamespace(correction_bias=model.gate.bias)
    layout = {'storages': [{'name': 'gate.bias', 'nbytes': 16}], 'tensors': [
        {'name': 'gate.bias', 'kind': 'param', 'storage_name': 'gate.bias', 'dtype': 'torch.float32',
         'shape': [4], 'stride': [1], 'storage_offset': 0}]}
    materialize(model, layout, 'meta')
    assert model.topk.config.correction_bias is model.gate.bias
    assert model.topk.config.correction_bias.is_meta
    materialize(model, layout, 'cpu')
    model.gate.bias.data.fill_(3)
    assert model.topk.config.correction_bias is model.gate.bias
    assert torch.equal(model.topk.config.correction_bias, torch.full((4,), 3.0))


def test_rebinding_preserves_shared_tuples_without_traversing_runtime_modules():
    import platform
    from collections import namedtuple
    from types import ModuleType
    model = torch.nn.Module()
    model.weight = torch.nn.Parameter(torch.ones(4))
    cached = (model.weight,)
    config = namedtuple('Routing', ['bias', 'system'])(model.weight, platform.uname())
    runtime = ModuleType('runtime')
    runtime.weight = model.weight
    model.config = SimpleNamespace(first=cached, second=cached, routing=config, runtime=runtime)
    layout = {'storages': [{'name': 'weight', 'nbytes': 16}], 'tensors': [
        {'name': 'weight', 'kind': 'param', 'storage_name': 'weight', 'dtype': 'torch.float32',
         'shape': [4], 'stride': [1], 'storage_offset': 0}]}
    previous = model.weight
    for device in ['meta', 'cpu']:
        materialize(model, layout, device)
        assert model.config.first is model.config.second
        assert model.config.first[0] is model.weight
        assert model.config.routing.bias is model.weight
        assert model.config.routing.system is config.system
        assert runtime.weight is previous


def test_distinct_rebound_views_never_become_accidental_aliases():
    from fluidckpt.serving.layout import bind
    model = torch.nn.Module()
    layout = {'tensors': []}
    buffers = {}
    for i in range(256):
        name = f'view_{i}'
        setattr(model, name, torch.zeros(8))
        buffers[name] = torch.full((8,), float(i)).view(torch.uint8)
        layout['tensors'].append({'name': name, 'kind': 'attr', 'storage_name': name,
            'dtype': 'torch.float32', 'shape': [8], 'stride': [1], 'storage_offset': 0})
    for _ in range(10):
        bind(model, layout, buffers)
        for i in range(256):
            tensor = getattr(model, f'view_{i}')
            assert torch.equal(tensor, torch.full((8,), float(i)))
            assert tensor.data_ptr() == buffers[f'view_{i}'].data_ptr()
