#!/usr/bin/env python3
"""Offline existing differential/native tests; never launch BIND/Knot or Doom."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
loader = unittest.TestLoader()
suite = loader.discover(str(ROOT / 'tests'), pattern='test_dns32*.py')
suite.addTests(loader.loadTestsFromName('test_cpu.CircuitTests'))
suite.addTests(loader.discover(str(ROOT / 'tests'), pattern='test_evidence.py'))
result = unittest.TextTestRunner(verbosity=2).run(suite)
if result.skipped:
    raise SystemExit('Offline checks skipped required cases: ' + repr(result.skipped))
raise SystemExit(not result.wasSuccessful())
