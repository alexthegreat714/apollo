from __future__ import annotations

import sys
import socket
from pathlib import Path

import pytest


_THIS_DIR = Path(__file__).resolve().parent
_APOLLO_ROOT = _THIS_DIR.parent
_ENGINEERING_ROOT = _APOLLO_ROOT.parent

for _path in (str(_ENGINEERING_ROOT), str(_APOLLO_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)


@pytest.fixture(autouse=True)
def _block_network_for_unit_tests(request, monkeypatch):
    if request.node.get_closest_marker("unit") is None or request.node.get_closest_marker("external") is not None:
        return

    def blocked(*args, **kwargs):
        raise AssertionError("unit test attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
