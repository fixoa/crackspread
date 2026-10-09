"""Shared pytest fixtures (ARCHITECTURE §7).

* puts ``<root>/scripts`` on ``sys.path`` so tests import modules by their flat names
  (``import common``, ``from fetch_prices import run``);
* ``cfg``, ``fixtures_dir``, ``tmp_data_dir``, ``now`` fixtures;
* an autouse ``no_network`` guard: ``common.http_get`` raises
  ``AssertionError("network call in test")`` unless the test is marked
  ``@pytest.mark.allow_network``. The same guard is installed on ``requests``, on ``urllib``
  (``urlopen`` *and* ``OpenerDirector.open``, which feedparser uses) and on ``socket.socket.connect``
  so that nothing in-process can open a TCP connection, whichever library it goes through.
  The guard is installed once per **session** (so module-scoped fixtures, which pytest sets up
  before any function-scoped fixture, are covered too) and re-asserted per test.
  Modules must call ``common.http_get`` through the module attribute (never
  ``from common import http_get``) for the fast, clear failure; anything else fails at the socket.
  Tests that exercise the real ``http_get`` with a stub session can request the ``no_network``
  fixture explicitly: it yields the original, unpatched function.
"""
from __future__ import annotations

import socket
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402  (needs sys.path above)

NOW = datetime(2026, 10, 8, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def cfg() -> dict:
    """The loaded ``site/config.json`` (a fresh copy per test)."""
    return common.load_config()


@pytest.fixture
def fixtures_dir() -> Path:
    """``tests/fixtures``."""
    return common.FIXTURES


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """An empty temporary data directory (stand-in for ``site/data``)."""
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def now() -> datetime:
    """The deterministic run time used by fixtures mode: 2026-10-08T10:00:00Z."""
    return NOW


#: The real callables, captured at import time (before any guard is installed).
_ORIGINALS = (
    (common, "http_get", common.http_get),
    (common, "http_post", common.http_post),
    (requests.sessions.Session, "request", requests.sessions.Session.request),
    (urllib.request, "urlopen", urllib.request.urlopen),
    (urllib.request.OpenerDirector, "open", urllib.request.OpenerDirector.open),
    # Last line of defence: no TCP connect at all (covers feedparser, http.client, urllib3, ...).
    (socket.socket, "connect", socket.socket.connect),
)
_REAL_HTTP_GET = common.http_get


def _blocked(*args, **kwargs):  # noqa: ANN001
    raise AssertionError("network call in test")


def _install_guard(mp: pytest.MonkeyPatch, blocked: bool = True) -> None:
    for owner, name, original in _ORIGINALS:
        mp.setattr(owner, name, _blocked if blocked else original)


@pytest.fixture(autouse=True, scope="session")
def _no_network_session():
    """Session-wide guard: module-scoped fixtures that run the fetchers in fixtures mode are
    instantiated before any function-scoped fixture, so the block must already be in place."""
    mp = pytest.MonkeyPatch()
    _install_guard(mp)
    yield
    mp.undo()


@pytest.fixture(autouse=True)
def no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Per test: re-assert the block (or lift it for ``allow_network``-marked tests) and yield the
    real, unpatched ``common.http_get`` for tests that drive it with a stub session."""
    allowed = request.node.get_closest_marker("allow_network") is not None
    _install_guard(monkeypatch, blocked=not allowed)
    yield _REAL_HTTP_GET
