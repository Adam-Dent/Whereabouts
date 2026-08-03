"""The placement tool's API.

This tool produces the only irreplaceable data in the project: thousands of
hand-placed coordinates that exist nowhere else and cannot be regenerated. The
tests that matter here are the ones about not losing it.

Note the deliberate patching below. `api_save` calls `_autocommit`, which shells
out to git and pushes. The socket guard in conftest cannot stop that, because a
subprocess has its own process and its own sockets, so both the placements
directory and the commit are redirected explicitly. A test run must never write
to data/placements/ or push to origin.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from etl import place_tool


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(place_tool, "PLACEMENTS_DIR", tmp_path / "placements")
    monkeypatch.setattr(place_tool, "_autocommit", lambda path, placed: False)
    return TestClient(place_tool.app)


@pytest.fixture
def a_sheet_id() -> str:
    sheets = place_tool._sheets()
    if not sheets:
        pytest.skip("no sheets built; run the ETL")
    return sheets[0]["id"]


def test_sheets_listing_returns_sheets(client: TestClient) -> None:
    resp = client.get("/api/sheets")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, (list, dict))


def test_unknown_sheet_is_a_404_not_a_500(client: TestClient) -> None:
    assert client.get("/api/sheet/no-such-sheet-anywhere").status_code == 404


def test_saving_to_an_unknown_sheet_is_refused(client: TestClient) -> None:
    """A typo in a sheet id must not create an orphan placements file that no
    build will ever read."""
    resp = client.post("/api/sheet/no-such-sheet-anywhere", json={"houses": {}})
    assert resp.status_code == 404


def test_save_then_read_round_trips(client: TestClient, a_sheet_id: str, tmp_path: Path) -> None:
    houses = {f"{a_sheet_id}-1": {"lat": 54.47, "lng": -1.82, "image_x": 100, "image_y": 200}}
    resp = client.post(f"/api/sheet/{a_sheet_id}", json={"alignment": None, "houses": houses})
    assert resp.status_code == 200
    assert resp.json()["placed"] == 1

    written = json.loads((tmp_path / "placements" / f"{a_sheet_id}.json").read_text())
    assert written["sheet_id"] == a_sheet_id
    assert written["houses"] == houses


def test_save_is_atomic_and_leaves_no_temp_file(
    client: TestClient, a_sheet_id: str, tmp_path: Path
) -> None:
    """The write goes to a .tmp and is renamed, so a crash mid-write cannot
    truncate a real placements file. The temp must not survive a good run."""
    client.post(f"/api/sheet/{a_sheet_id}", json={"houses": {f"{a_sheet_id}-1": {"lat": 54.4, "lng": -1.8}}})
    leftovers = list((tmp_path / "placements").glob("*.tmp"))
    assert not leftovers, f"temp files left behind: {leftovers}"


def test_a_later_save_replaces_the_earlier_one(
    client: TestClient, a_sheet_id: str, tmp_path: Path
) -> None:
    client.post(f"/api/sheet/{a_sheet_id}", json={"houses": {f"{a_sheet_id}-1": {"lat": 54.1, "lng": -1.1}}})
    client.post(f"/api/sheet/{a_sheet_id}", json={"houses": {f"{a_sheet_id}-2": {"lat": 54.2, "lng": -1.2}}})
    written = json.loads((tmp_path / "placements" / f"{a_sheet_id}.json").read_text())
    assert list(written["houses"]) == [f"{a_sheet_id}-2"]


def test_saving_an_empty_placement_set_is_allowed(client: TestClient, a_sheet_id: str) -> None:
    """Clearing a sheet is legitimate: the tool must be able to undo a bad pass."""
    resp = client.post(f"/api/sheet/{a_sheet_id}", json={"houses": {}})
    assert resp.status_code == 200
    assert resp.json()["placed"] == 0


def test_the_save_records_the_pdf_hash(client: TestClient, a_sheet_id: str, tmp_path: Path) -> None:
    """Placements are tied to the PDF they were made against. If Colin reissues
    a sheet with a different layout, the hash is what says the coordinates may
    no longer mean what they did."""
    client.post(f"/api/sheet/{a_sheet_id}", json={"houses": {}})
    written = json.loads((tmp_path / "placements" / f"{a_sheet_id}.json").read_text())
    assert "pdf_hash" in written


def test_search_endpoint_needs_no_network_for_an_empty_query(client: TestClient) -> None:
    """An empty query short-circuits before Nominatim, which is what makes this
    endpoint testable at all under the no-network guard."""
    resp = client.get("/api/search", params={"q": "   "})
    assert resp.status_code == 200
    assert resp.json() == []
