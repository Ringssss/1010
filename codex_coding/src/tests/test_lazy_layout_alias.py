"""Permit lazy module aliases while rejecting changed or newly required bytes."""
import pytest
from fluidckpt.serving.layout import validate_tensor_layout


@pytest.fixture
def view():
    return {'name': 'attention.kv.weight', 'storage_name': 'attention.kv.weight', 'kind': 'param',
            'dtype': 'torch.float8_e4m3fn', 'shape': [1792, 512], 'stride': [512, 1],
            'storage_offset': 0, 'attributes': {'format_ue8m0': False}}


def test_exact_lazy_alias_is_accepted(view):
    validate_tensor_layout([view], [view, dict(view, name='attention.mha.kv.weight')])


@pytest.mark.parametrize('change', [{'dtype': 'torch.uint8'}, {'storage_offset': 1},
    {'storage_name': 'other.weight'}, {'shape': [512, 1792]}, {'attributes': {'format_ue8m0': True}}])
def test_extra_name_cannot_change_storage_semantics(view, change):
    with pytest.raises(ValueError):
        validate_tensor_layout([view], [view, dict(view, name='attention.mha.kv.weight', **change)])


def test_required_tensor_cannot_be_renamed_or_redefined(view):
    with pytest.raises(ValueError):
        validate_tensor_layout([view], [dict(view, name='renamed')])
    with pytest.raises(ValueError):
        validate_tensor_layout([view], [dict(view, storage_offset=1)])
