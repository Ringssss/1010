"""Rebuild tensor views from a captured storage layout, including aliases."""
import functools
import json
import types


def scalar_attributes(tensor):
    """Carry runtime flags such as FP8 scale formats without retaining tensor allocations."""
    return {key: value for key, value in getattr(tensor, '__dict__', {}).items()
            if value is None or type(value) in (str, bool, int, float)}


def parent(model, name):
    parts = name.split('.')
    return functools.reduce(getattr, parts[:-1], model), parts[-1]


def validate_tensor_layout(expected, actual):
    """Accept new names only when they describe an existing storage view exactly."""
    if expected == actual:
        return
    if not isinstance(expected, list) or not isinstance(actual, list):
        raise ValueError('snapshot identity/layout mismatch: tensors')
    old = {row['name']: row for row in expected}
    new = {row['name']: row for row in actual}
    if len(old) != len(expected) or len(new) != len(actual):
        raise ValueError('duplicate tensor name in layout')
    if any(new.get(name) != row for name, row in old.items()):
        raise ValueError('snapshot identity/layout mismatch: existing tensor changed or disappeared')
    def signature(row):
        return json.dumps({key: value for key, value in row.items() if key != 'name'}, sort_keys=True)
    signatures = {signature(row) for row in expected}
    if any(signature(row) not in signatures for name, row in new.items() if name not in old):
        raise ValueError('snapshot identity/layout mismatch: extra tensor is not an exact existing view')


def bind(model, layout, storages):
    import torch
    replacements = {}
    previous_tensors = []
    for row in layout['tensors']:
        storage = storages[row['storage_name']]
        dtype = getattr(torch, row['dtype'].split('.')[-1])
        tensor = storage.view(dtype).as_strided(row['shape'], row['stride'], row['storage_offset'])
        module, name = parent(model, row['name'])
        previous = getattr(module, name, None)
        attributes = dict(row['attributes']) if 'attributes' in row else scalar_attributes(previous)
        if row['kind'] == 'param':
            tensor = torch.nn.Parameter(tensor, requires_grad=False)
        for key, value in attributes.items():
            setattr(tensor, key, value)
        if torch.is_tensor(previous):
            # IDs remain unique until every nested reference has been rewritten.
            previous_tensors.append(previous)
            replacements[id(previous)] = tensor
        setattr(module, name, tensor)
    # Routing configs and other plain Python containers can retain parameter objects.
    # Rebinding the registered parameter alone leaves those references on the old device.
    rewritten = {}
    def rewrite(value, depth=0):
        if torch.is_tensor(value):
            return replacements.get(id(value), value)
        if depth > 8 or isinstance(value, (torch.nn.Module, types.ModuleType, type)) or callable(value):
            return value
        if id(value) in rewritten:
            return rewritten[id(value)]
        rewritten[id(value)] = value
        if isinstance(value, dict):
            for key, child in list(value.items()): value[key] = rewrite(child, depth+1)
        elif isinstance(value, list):
            for i, child in enumerate(value): value[i] = rewrite(child, depth+1)
        elif isinstance(value, tuple):
            values = [rewrite(child, depth+1) for child in value]
            if any(new is not old for new, old in zip(values, value)):
                if hasattr(value, '_replace'):
                    replacement = value._replace(**{key: child for key, child in zip(value._fields, values)})
                elif type(value) is tuple:
                    replacement = tuple(values)
                else:
                    raise TypeError(f'unsupported tensor-bearing tuple: {type(value)}')
                rewritten[id(value)] = replacement
                return replacement
        elif hasattr(value, '__dict__'):
            for key, child in list(vars(value).items()):
                replacement = rewrite(child, depth+1)
                if replacement is not child: setattr(value, key, replacement)
        return value
    for module in model.modules():
        for name, value in list(vars(module).items()):
            if name not in ('_parameters', '_buffers', '_modules'):
                replacement = rewrite(value)
                if replacement is not value: setattr(module, name, replacement)


def materialize(model, layout, device):
    import torch
    storages = {row['name']: torch.empty(row['nbytes'], dtype=torch.uint8, device=device)
                for row in layout['storages']}
    bind(model, layout, storages)
    return sum(row['nbytes'] for row in layout['storages'])
