"""Shared fixtures, and a hard ban on network access from the test suite.

Two of the services this project talks to belong to other people: Colin Day's
web server, which hosts the maps everything here is built on, and Nominatim.
CI runs on every push, so a test that quietly reached for either would turn
into a small robot hammering someone else's site forever. The guard below makes
that impossible rather than merely discouraged.

It also keeps the suite honest: a test that needs the network is a test whose
result depends on someone else's uptime, and there are none of those here.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
DIST = REPO / "data" / "dist"
DOCS = REPO / "docs"


class NetworkAccessAttempted(RuntimeError):
    """Raised instead of opening a socket, so the traceback names the caller."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that tries to open a network connection.

    Patched at the socket layer rather than at `requests`, so it catches every
    client library, including anything a dependency reaches for internally.
    Loopback stays open: the FastAPI tests talk to an in-process app, and
    blocking localhost would break them for no benefit.
    """
    real_connect = socket.socket.connect

    def guarded(self: socket.socket, address, *args, **kwargs):  # type: ignore[no-untyped-def]
        host = address[0] if isinstance(address, tuple) else address
        if host in ("127.0.0.1", "::1", "localhost"):
            return real_connect(self, address, *args, **kwargs)
        raise NetworkAccessAttempted(
            f"the test suite tried to reach {host!r}. Tests must not use the "
            f"network: use a fixture instead. See etl/tests/conftest.py."
        )

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture(scope="session")
def shipped() -> dict:
    """The dataset the PWA actually serves, as deployed."""
    path = DOCS / "houses.json"
    if not path.exists():
        pytest.skip("docs/houses.json not built; run whereabouts-build-pwa")
    return json.loads(path.read_text())


@pytest.fixture(scope="session")
def parsed_houses() -> list[dict]:
    """Raw parser output, before the PWA drops nameless records."""
    path = DIST / "houses.json"
    if not path.exists():
        pytest.skip("data/dist/houses.json not present; run the ETL")
    return json.loads(path.read_text())


@pytest.fixture(scope="session")
def parsed_sheets() -> list[dict]:
    path = DIST / "sheets.json"
    if not path.exists():
        pytest.skip("data/dist/sheets.json not present; run the ETL")
    return json.loads(path.read_text())


def word(text: str, x0: float, top: float, width: float = None, height: float = 9.0) -> dict:
    """A pdfplumber-shaped word/char dict, which is what the parser works on.

    Real pdfplumber output carries more keys than this, but the parser only
    reads these five, so building them by hand keeps the edge-case tests
    readable and free of PDF fixtures.
    """
    w = width if width is not None else len(text) * 5.0
    return {"text": text, "x0": x0, "x1": x0 + w, "top": top, "bottom": top + height}
