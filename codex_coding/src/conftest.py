"""Put the core library on sys.path so tests can `import fluidckpt` without PYTHONPATH.

The library lives in main_lib/ at the repo root; the scripts in this tree are consumers.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "main_lib"))
