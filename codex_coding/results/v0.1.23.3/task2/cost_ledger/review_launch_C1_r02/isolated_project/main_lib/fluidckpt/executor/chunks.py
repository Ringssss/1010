"""Serial chunk choices share the existing 64-MiB staging budget."""
DEFAULT_CHUNK = 32 << 20
STAGING_BYTES = 2*DEFAULT_CHUNK


def execution_chunk(plan):
    executor = plan.get('executor', 'serial')
    size = plan.get('chunk_bytes', DEFAULT_CHUNK)
    if executor not in ('serial', 'double_buffer'):
        raise ValueError('unsupported executor')
    if type(size) is not int or size not in (DEFAULT_CHUNK, STAGING_BYTES):
        raise ValueError('unsupported chunk size')
    if executor == 'double_buffer' and size != DEFAULT_CHUNK:
        raise ValueError('double buffer requires two 32-MiB slots')
    return size
