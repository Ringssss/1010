"""Exercise capture blocks that cross several storage boundaries and include a tail."""
import torch
from fluidckpt.serving.keeper import HbmTarget


def test_gather_storage_boundaries_and_partial_tail():
    source = HbmTarget.__new__(HbmTarget)
    expected = torch.arange(41, dtype=torch.uint8)
    source.segs = [(0, 9, expected[:9]), (9, 21, expected[9:30]), (30, 11, expected[30:])]
    source.starts = [0, 9, 30]
    for off, n in [(0, 16), (16, 16), (32, 9), (8, 24)]:
        result = torch.empty(n, dtype=torch.uint8)
        assert source.gather(off, result) == n
        assert torch.equal(result, expected[off:off+n])
