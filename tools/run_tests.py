"""Run all tests without putting native windows on the user's desktop."""
from pathlib import Path
import sys
import unittest
from background_desktop import isolate

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if __name__ == '__main__':
    desktop = isolate()
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())
