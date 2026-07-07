"""FastAPI + Leaflet georeferencing tool (spec §5.4)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .geocode import geocode_village
from .models import ControlPoint, ControlPointsFile, PagePos, LatLng
from .transform import fit_affine, apply_affine


DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"
DIST_DIR = DATA_DIR / "dist"
CP_DIR = DATA_DIR / "control_points"

app = FastAPI(title="Whereabouts Georeferencing Tool")

# Mount rendered images so the UI can load them
app.mount("/images", StaticFiles(directory=str(DIST_DIR / "images")), name="images")


def _load_sheets() -> list[dict]:
    sheets_path = DIST_DIR / "sheets.json"
    if not sheets_path.exists():
        return []
    return json.loads(sheets_path.read_text())


def _load_cp(sheet_id: str) -> ControlPointsFile | None:
    p = CP_DIR / f"{sheet_id}.json"
    if not p.exists():
        return None
    raw = json.loads(p.read_text())
    return ControlPointsFile(
        sheet_id=raw["sheet_id"],
        pdf_hash=raw["pdf_hash"],
        points=[
            ControlPoint(
                page=PagePos(**pt["page"]),
                world=LatLng(**pt["world"]),
            )
            for pt in raw["points"]
        ],
    )


def _save_cp(cpf: ControlPointsFile) -> None:
    CP_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "sheet_id": cpf.sheet_id,
        "pdf_hash": cpf.pdf_hash,
        "points": [
            {"page": {"x": pt.page.x, "y": pt.page.y}, "world": {"lat": pt.world.lat, "lng": pt.world.lng}}
            for pt in cpf.points
        ],
    }
    (CP_DIR / f"{cpf.sheet_id}.json").write_text(json.dumps(data, indent=2))


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    sheets = _load_sheets()
    cp_files = {p.stem for p in CP_DIR.glob("*.json")}

    rows = []
    for s in sheets:
        sid = s["id"]
        status = "done" if sid in cp_files else "pending"
        rows.append(f'<tr><td><a href="/georeference/{sid}">{sid}</a></td>'
                    f'<td>{s["village_name"]}</td><td>{status}</td></tr>')

    return f"""<!DOCTYPE html>
<html><head><title>Whereabouts Georef</title></head><body>
<h1>Georeferencing Tool</h1>
<table border="1">
<tr><th>Sheet</th><th>Village</th><th>Status</th></tr>
{"".join(rows)}
</table>
</body></html>"""


@app.get("/georeference/{sheet_id}", response_class=HTMLResponse)
async def georeference_page(sheet_id: str) -> str:
    sheets = _load_sheets()
    sheet = next((s for s in sheets if s["id"] == sheet_id), None)
    if not sheet:
        return HTMLResponse(f"Sheet {sheet_id} not found", status_code=404)

    village_name = sheet["village_name"]
    geocoded = geocode_village(village_name)
    centre_lat = geocoded[0] if geocoded else 54.35
    centre_lng = geocoded[1] if geocoded else -1.72

    cpf = _load_cp(sheet_id)
    existing_points = json.dumps(
        [{"page": {"x": p.page.x, "y": p.page.y}, "world": {"lat": p.world.lat, "lng": p.world.lng}}
         for p in cpf.points]
        if cpf else []
    )
    pdf_hash = sheet.get("pdf_hash", "")

    return f"""<!DOCTYPE html>
<html><head>
<title>Georeference: {sheet_id}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
  body {{ margin: 0; display: flex; flex-direction: column; height: 100vh; font-family: sans-serif; }}
  #toolbar {{ padding: 8px; background: #222; color: #eee; display: flex; gap: 12px; align-items: center; }}
  #main {{ display: flex; flex: 1; overflow: hidden; }}
  #map-panel {{ flex: 1; overflow: auto; background: #333; display: flex; align-items: flex-start; justify-content: center; padding: 8px; }}
  #map-canvas {{ position: relative; cursor: crosshair; }}
  #map-canvas img {{ display: block; max-width: 100%; }}
  #slippy {{ flex: 1; }}
  #status {{ padding: 4px 12px; background: #f5f5f5; font-size: 0.85em; }}
  .cp-dot {{ position: absolute; width: 10px; height: 10px; border-radius: 50%; background: red; transform: translate(-50%,-50%); pointer-events: none; }}
</style>
</head><body>
<div id="toolbar">
  <strong>{sheet_id}</strong>: {village_name}
  <button onclick="solve()">Solve</button>
  <button onclick="savePoints()">Save</button>
  <button onclick="clearLast()">Undo last</button>
  <span id="rms"></span>
</div>
<div id="main">
  <div id="map-panel">
    <div id="map-canvas">
      <img id="sheet-img" src="/images/{sheet_id}.png" alt="{sheet_id}"/>
    </div>
  </div>
  <div id="slippy"></div>
</div>
<div id="status">Click a recognisable feature on the drawing (left), then the same spot on the map (right). Repeat ≥3 times, then Solve.</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const SHEET_ID = {json.dumps(sheet_id)};
const PDF_HASH = {json.dumps(pdf_hash)};
let points = {existing_points};
let pendingPage = null;
let previewMarkers = [];

// Slippy map
const slippy = L.map('slippy').setView([{centre_lat}, {centre_lng}], 14);
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution: '© OpenStreetMap contributors', maxZoom: 19
}}).addTo(slippy);

// Drawing side
const img = document.getElementById('sheet-img');
const canvas = document.getElementById('map-canvas');

img.onload = () => redrawDots();

canvas.addEventListener('click', e => {{
  const rect = img.getBoundingClientRect();
  const scaleX = img.naturalWidth / rect.width;
  const scaleY = img.naturalHeight / rect.height;
  // Convert to image pixels (= page coords * render_scale, but we store image pixels
  // and the API layer converts back as needed)
  const px = (e.clientX - rect.left) * scaleX;
  const py = (e.clientY - rect.top) * scaleY;
  pendingPage = {{x: px, y: py}};
  document.getElementById('status').textContent =
    `Image click at (${{px.toFixed(1)}}, ${{py.toFixed(1)}}). Now click the same spot on the map.`;
}});

slippy.on('click', e => {{
  if (!pendingPage) {{
    document.getElementById('status').textContent = 'Click the drawing first, then the map.';
    return;
  }}
  points.push({{page: pendingPage, world: {{lat: e.latlng.lat, lng: e.latlng.lng}}}});
  pendingPage = null;
  redrawDots();
  document.getElementById('status').textContent =
    `Point ${{points.length}} added. Add more or click Solve.`;
}});

function redrawDots() {{
  // Remove old dots
  canvas.querySelectorAll('.cp-dot').forEach(d => d.remove());
  const rect = img.getBoundingClientRect();
  const scaleX = rect.width / img.naturalWidth;
  const scaleY = rect.height / img.naturalHeight;
  points.forEach((pt, i) => {{
    const dot = document.createElement('div');
    dot.className = 'cp-dot';
    dot.style.left = (pt.page.x * scaleX) + 'px';
    dot.style.top = (pt.page.y * scaleY) + 'px';
    dot.title = `CP ${{i+1}}`;
    canvas.appendChild(dot);
  }});
}}

window.addEventListener('resize', redrawDots);

async function solve() {{
  if (points.length < 3) {{ alert('Need at least 3 control points'); return; }}
  const resp = await fetch('/api/solve', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{sheet_id: SHEET_ID, pdf_hash: PDF_HASH, points}})
  }});
  const result = await resp.json();
  document.getElementById('rms').textContent = `RMS: ${{result.rms_m.toFixed(1)}} m`;

  // Show preview markers on slippy map
  previewMarkers.forEach(m => slippy.removeLayer(m));
  previewMarkers = [];
  (result.preview_houses || []).forEach(h => {{
    const m = L.circleMarker([h.lat, h.lng], {{radius: 4, color: 'red'}})
      .bindTooltip(h.names.join(', '));
    m.addTo(slippy);
    previewMarkers.push(m);
  }});
}}

async function savePoints() {{
  if (points.length < 3) {{ alert('Need at least 3 control points'); return; }}
  await fetch('/api/save', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{sheet_id: SHEET_ID, pdf_hash: PDF_HASH, points}})
  }});
  document.getElementById('status').textContent = 'Saved!';
}}

function clearLast() {{
  if (points.length) points.pop();
  pendingPage = null;
  redrawDots();
}}
</script>
</body></html>"""


@app.post("/api/solve")
async def api_solve(body: dict) -> JSONResponse:
    from .models import ControlPoint, ControlPointsFile, PagePos, LatLng

    cpf = ControlPointsFile(
        sheet_id=body["sheet_id"],
        pdf_hash=body.get("pdf_hash", ""),
        points=[
            ControlPoint(page=PagePos(**p["page"]), world=LatLng(**p["world"]))
            for p in body["points"]
        ],
    )
    try:
        affine, rms = fit_affine(cpf)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    # Load houses for this sheet to show preview
    houses_path = DIST_DIR / "houses.json"
    preview: list[dict] = []
    if houses_path.exists():
        all_houses = json.loads(houses_path.read_text())
        for h in all_houses:
            if h["sheet_id"] == body["sheet_id"] and h["page_pos"]:
                pp = PagePos(**h["page_pos"])
                lng, lat = apply_affine(affine, pp)
                preview.append({"names": h["names"], "lat": lat, "lng": lng})

    return JSONResponse({"rms_m": rms, "affine": affine, "preview_houses": preview})


@app.post("/api/save")
async def api_save(body: dict) -> JSONResponse:
    from .models import ControlPoint, ControlPointsFile, PagePos, LatLng

    cpf = ControlPointsFile(
        sheet_id=body["sheet_id"],
        pdf_hash=body.get("pdf_hash", ""),
        points=[
            ControlPoint(page=PagePos(**p["page"]), world=LatLng(**p["world"]))
            for p in body["points"]
        ],
    )
    _save_cp(cpf)
    return JSONResponse({"saved": True})


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Whereabouts georeferencing tool")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run("etl.georef_tool:app", host=args.host, port=args.port, reload=True)
