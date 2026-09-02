#!/usr/bin/env python3
"""Thin entry point so `./opensearch-analyze.py` keeps working without an
editable install. The real implementation lives in src/opensearch_analyze
(see that package for docs) so it's a proper importable package for
pytest/pyright/ruff; `uv run opensearch-analyze` uses the same code via the
console-script entry point in pyproject.toml.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from opensearch_analyze import main

if __name__ == "__main__":
    sys.exit(main())
