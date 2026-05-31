"""
pytest configuration — ensures the backend root is on sys.path
so that `core`, `services`, and top-level modules are all importable.
"""
import sys
import os

# Add the backend directory root to sys.path
sys.path.insert(0, os.path.dirname(__file__))
