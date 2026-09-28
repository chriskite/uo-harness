"""Phase 2 world-model test entry point.

Runs the parser/runtime unit suite (test_world_units.py) and the session
replay suite (test_world_replay.py). Usage: python harness/test_world.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_world_units
import test_world_replay


def main():
    rc1 = test_world_units.main()
    print()
    rc2 = test_world_replay.main()
    ok = rc1 == 0 and rc2 == 0
    print(f"\nworld model: {'ALL PASS' if ok else 'FAILURES'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
