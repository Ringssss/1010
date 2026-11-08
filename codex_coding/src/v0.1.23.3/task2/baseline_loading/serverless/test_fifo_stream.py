"""Exercise real FIFO delays, short sources and failed consumers without GPU or NFS IO."""
import hashlib
from pathlib import Path
import tempfile
import threading
import time
import unittest

from fifo_stream import export_fifo_to_file


class FailedDisk:
    def open(self, *args, **kwargs):
        return self
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def write(self, block):
        raise OSError("injected disk failure")


class FifoGate(unittest.TestCase):
    def test_delayed_writer_and_all_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = bytes(range(256)) * 4096
            def producer():
                with (root / "source.fifo").open("wb", buffering=0) as stream:
                    time.sleep(.03)
                    stream.write(payload)
                return "saved"
            actual, saved = export_fifo_to_file(root / "source.fifo", root / "out.bin", len(payload), producer, 3)
            self.assertEqual(actual, hashlib.sha256(payload).hexdigest())
            self.assertEqual((root / "out.bin").read_bytes(), payload)
            self.assertEqual(saved, "saved")

    def test_short_source_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def producer():
                with (root / "source.fifo").open("wb", buffering=0) as stream:
                    stream.write(b"short")
            with self.assertRaises(EOFError):
                export_fifo_to_file(root / "source.fifo", root / "out.bin", 4096, producer, 3)
            self.assertFalse((root / "source.fifo").exists())

    def test_failed_consumer_unblocks_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            broken_pipe = threading.Event()
            def producer():
                try:
                    with (root / "source.fifo").open("wb", buffering=0) as stream:
                        while True:
                            stream.write(bytes(65536))
                except BrokenPipeError:
                    broken_pipe.set()
                    raise
            started = time.monotonic()
            with self.assertRaisesRegex(OSError, "injected disk failure"):
                export_fifo_to_file(root / "source.fifo", FailedDisk(), 1 << 20, producer, 3)
            self.assertTrue(broken_pipe.is_set())
            self.assertLess(time.monotonic()-started, 2)
            self.assertFalse((root / "source.fifo").exists())


if __name__ == "__main__":
    unittest.main()
