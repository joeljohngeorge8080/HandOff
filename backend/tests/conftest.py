from __future__ import annotations

from pathlib import Path

import pytest

from handoff.core import Core
from handoff.paths import AppPaths


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    """Isolated app-data directory. Tests never touch a real HandOff install."""
    return AppPaths(tmp_path / "data")


@pytest.fixture
def core(paths: AppPaths):
    c = Core(paths, hostname="Test-Laptop")
    c.start()
    yield c
    c.close()


@pytest.fixture
def make_file(tmp_path: Path):
    """Create a throwaway source file outside HandOff storage."""
    src = tmp_path / "source"
    src.mkdir(exist_ok=True)

    def _make(name: str, content: bytes = b"hello") -> Path:
        p = src / name
        p.write_bytes(content)
        return p

    return _make


@pytest.fixture
def h(tmp_path: Path):
    """Receiver HTTP app (no sockets) plus signing identities `alice` and `bob`."""
    from helpers import PeerHarness

    harness = PeerHarness(tmp_path)
    yield harness
    harness.close()


@pytest.fixture
def node_factory(tmp_path: Path):
    """Create full nodes (real HTTPS on 127.0.0.1); all are stopped at teardown."""
    from helpers import TestNode

    made: list = []

    def make(name: str, **net):
        n = TestNode(tmp_path, name, **net)
        made.append(n)
        return n

    yield make
    for n in made:
        n.stop()


@pytest.fixture
def gate():
    """Pause an upload mid-stream; always released at teardown so nothing hangs."""
    from helpers import Gate

    g = Gate()
    yield g
    g.release.set()
