from __future__ import annotations

import sys
from pathlib import Path


_THIS_DIR = Path(__file__).resolve().parent
_APOLLO_ROOT = _THIS_DIR.parent
_ENGINEERING_ROOT = _APOLLO_ROOT.parent

for _path in (str(_ENGINEERING_ROOT), str(_APOLLO_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
