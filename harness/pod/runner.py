#!/usr/bin/env python3
"""Deploy with the harness modules; delegates to pods.runner_main."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pods import runner_main

if __name__ == '__main__':
    raise SystemExit(runner_main())
