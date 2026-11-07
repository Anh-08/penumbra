"""Shared test setup for backend/: offline by default, and no outbound network."""
import os
import socket

import pytest

os.environ.setdefault("PENUMBRA_OFFLINE", "1")

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_real_connect = socket.socket.connect


def _guarded_connect(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, str) and host not in _LOOPBACK and self.family in (socket.AF_INET, socket.AF_INET6):
        raise RuntimeError(f"network access in tests is forbidden (tried {address!r})")
    return _real_connect(self, address)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    yield
