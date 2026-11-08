"""Compatibility imports for the original profiling scripts."""
from fluidckpt.memory.numa import HostBuffer, mbind, node_of, placement

__all__ = ["HostBuffer", "mbind", "node_of", "placement"]
