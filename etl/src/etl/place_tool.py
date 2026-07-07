"""House-placement tool: overlay a village drawing on satellite imagery, then
click each named house to capture its coordinate.

This replaces the control-point/affine georeferencing approach. The rendered PDF
is shown as a warpable, semi-transparent overlay on a satellite basemap; once it
is aligned over the real village, clicking the building for a named house records
both its lat/lng (for navigation) and its pixel on the drawing (for the app's
highlight ring), derived from the alignment.

Per-village results are saved to ``data/placements/<sheet_id>.json`` — the source
of truth, version-controlled and merged into the dataset by the ETL.

Run:  uv run uvicorn etl.place_tool:app --reload   (then open http://127.0.0.1:8000)
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"
DIST_DIR = DATA_DIR / "dist"
PLACEMENTS_DIR = DATA_DIR / "placements"

app = FastAPI(title="Whereabouts — House Placement Tool")
app.mount("/images", StaticFiles(directory=str(DIST_DIR / "images")), name="images")

from .pwa import router as _pwa_router  # noqa: E402
app.include_router(_pwa_router)


def _load_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def _sheets() -> list[dict]:
    return _load_json(DIST_DIR / "sheets.json", [])


def _houses() -> list[dict]:
    return _load_json(DIST_DIR / "houses.json", [])


def _villages() -> list[dict]:
    return _load_json(DIST_DIR / "villages.json", [])


def _placements(sheet_id: str) -> dict:
    return _load_json(PLACEMENTS_DIR / f"{sheet_id}.json", {})


@app.get("/api/sheets")
def api_sheets() -> JSONResponse:
    """Every sheet, with village name, image, and progress (placed/total)."""
    houses = _houses()
    by_sheet: dict[str, int] = {}
    named_by_sheet: dict[str, int] = {}
    for h in houses:
        sid = h["sheet_id"]
        by_sheet[sid] = by_sheet.get(sid, 0) + 1
        primary = (h.get("names") or [""])[0]
        if primary and not primary[0].isdigit():
            named_by_sheet[sid] = named_by_sheet.get(sid, 0) + 1
    out = []
    for s in _sheets():
        placed = len(_placements(s["id"]).get("houses", {}))
        out.append({
            "id": s["id"],
            "village_name": s["village_name"],
            "district": s["district"],
            "image_path": s["image_path"],
            "image_size": s["image_size"],
            "total": by_sheet.get(s["id"], 0),
            "placed": placed,
            "named": named_by_sheet.get(s["id"], 0),
        })
    out.sort(key=lambda s: -s["named"])
    return JSONResponse(out)


@app.get("/api/sheet/{sheet_id}")
def api_sheet(sheet_id: str) -> JSONResponse:
    """Houses for one sheet plus any saved alignment and placements."""
    sheet = next((s for s in _sheets() if s["id"] == sheet_id), None)
    if sheet is None:
        raise HTTPException(404, f"Unknown sheet {sheet_id}")
    houses = [
        {"id": h["id"], "map_number": h["map_number"], "names": h["names"]}
        for h in _houses() if h["sheet_id"] == sheet_id
    ]
    houses.sort(key=lambda h: h["map_number"])
    village = next((v for v in _villages() if v["id"] == sheet.get("village_id")), None)
    saved = _placements(sheet_id)
    return JSONResponse({
        "sheet": sheet,
        "centroid": village.get("centroid") if village else None,
        "houses": houses,
        "alignment": saved.get("alignment"),
        "placements": saved.get("houses", {}),
    })


@app.get("/api/search")
def api_search(q: str) -> JSONResponse:
    """Free-text place search via Nominatim (to find a village on the map)."""
    if not q.strip():
        return JSONResponse([])
    resp = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": q, "format": "json", "limit": 6, "countrycodes": "gb"},
        headers={"User-Agent": "Whereabouts-PlaceTool/0.1"},
        timeout=10,
    )
    resp.raise_for_status()
    out = [
        {"name": r["display_name"], "lat": float(r["lat"]), "lng": float(r["lon"])}
        for r in resp.json()
    ]
    return JSONResponse(out)


@app.post("/api/sheet/{sheet_id}")
async def api_save(sheet_id: str, body: dict) -> JSONResponse:
    """Persist alignment corners and per-house coordinates for a sheet."""
    sheet = next((s for s in _sheets() if s["id"] == sheet_id), None)
    if sheet is None:
        raise HTTPException(404, f"Unknown sheet {sheet_id}")
    PLACEMENTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "sheet_id": sheet_id,
        "pdf_hash": sheet.get("pdf_hash"),
        "alignment": body.get("alignment"),
        "houses": body.get("houses", {}),
    }
    path = PLACEMENTS_DIR / f"{sheet_id}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)  # atomic: a crash mid-write can never truncate the real file
    committed = _autocommit(path, len(payload["houses"]))
    return JSONResponse({"ok": True, "placed": len(payload["houses"]), "committed": committed})


def _autocommit(path: Path, placed: int) -> bool:
    """Commit the saved placement file so placement work is never lost.

    The commit is synchronous (fast, local); the push is fire-and-forget so a
    dead network can't slow down or break saving.
    """
    repo = DATA_DIR.parent
    rel = path.relative_to(repo)
    try:
        subprocess.run(["git", "add", str(rel)], cwd=repo, check=True,
                       capture_output=True, timeout=10)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet", "--", str(rel)],
                              cwd=repo, capture_output=True, timeout=10)
        if diff.returncode == 0:
            return False  # no change since last commit
        subprocess.run(
            ["git", "commit", "-m", f"Place {path.stem}: {placed} houses (auto-save)",
             "--", str(rel)],
            cwd=repo, check=True, capture_output=True, timeout=15,
        )
        subprocess.Popen(["git", "push", "origin", "HEAD"], cwd=repo,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (subprocess.SubprocessError, OSError) as e:
        print(f"placement auto-commit failed (file IS saved): {e}")
        return False


@app.get("/api/app/houses")
def api_app_houses() -> JSONResponse:
    """All houses merged with placement data — for the front-end app."""
    from .pwa import _merged_houses
    return JSONResponse(_merged_houses())


@app.get("/app", response_class=HTMLResponse)
def whereabouts_app() -> str:
    return _APP_PAGE


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _PAGE


_PAGE = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Whereabouts — Place Houses</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
  html,body{margin:0;height:100%;font-family:system-ui,sans-serif}
  #app{display:flex;height:100%}
  #side{width:310px;flex:none;display:flex;flex-direction:column;border-right:1px solid #ccc;background:#fafafa}
  #map{flex:1}
  #side h1{font-size:15px;margin:10px 12px 4px}
  #bar{padding:6px 12px;border-bottom:1px solid #ddd}
  #stats{display:flex;gap:6px;padding:10px 12px 0}
  .statbox{flex:1;min-width:0;background:#eef3ff;border:1px solid #cdd;border-radius:6px;
    padding:7px 4px;text-align:center}
  .statlabel{font-size:10px;color:#678;text-transform:uppercase;letter-spacing:.03em;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .statpct{font-size:19px;font-weight:700;color:#1b3d5f;margin-top:2px}
  select,button,input{font:inherit}
  #district,#village,#searchq{width:100%;box-sizing:border-box}
  #district{margin-bottom:6px;color:#555}
  .row{display:flex;gap:6px;margin-top:6px}
  #results{max-height:130px;overflow:auto}
  #results div{font-size:12px;padding:4px 12px;border-bottom:1px solid #eee;cursor:pointer}
  #results div:hover{background:#eef}
  #step{padding:8px 12px;background:#eef3ff;border-bottom:1px solid #cdd}
  #stepttl{font-size:13px;font-weight:600}
  #stepbtn{width:100%;margin-top:6px;padding:7px;font-weight:600}
  #status{font-size:12px;color:#555;padding:4px 12px}
  #list{flex:1;overflow:auto}
  #list.dim{opacity:.4;pointer-events:none}
  .house{padding:7px 12px;border-bottom:1px solid #eee;cursor:pointer;font-size:13px}
  .house:hover{background:#eef}
  .house.sel{background:#d8e6ff}
  .house.done{color:#176}
  .house .num{display:inline-block;width:26px;color:#999}
  .house .tick{float:right;color:#1a8}
  .alias{color:#999;font-size:11px;margin-left:26px}
  .scalehandle{width:20px;height:20px;background:#fff;border:3px solid #d22;border-radius:4px;
    box-shadow:0 0 4px #0008;cursor:nwse-resize;box-sizing:border-box}
  .warphandle{width:14px;height:14px;background:#fff;border:3px solid #22d;border-radius:50%;
    box-shadow:0 0 4px #0008;cursor:move;box-sizing:border-box}
  #warpbtn.active{background:#22d;color:#fff;border-color:#11b}
  #warpimg{position:absolute;transform-origin:0 0;cursor:move;will-change:transform}
  #actions{padding:8px 12px;border-top:1px solid #ddd;display:flex;gap:6px;flex-wrap:wrap}
  .hint{font-size:11px;color:#777;padding:0 12px 8px}
  kbd{background:#eee;border:1px solid #ccc;border-radius:3px;padding:0 4px;font-size:11px}
</style>
</head>
<body>
<div id="app">
  <div id="side">
    <h1>Whereabouts — Place Houses</h1>
    <div id="stats">
      <div class="statbox"><div class="statlabel">Overall</div><div class="statpct" id="stat-overall">–</div></div>
      <div class="statbox"><div class="statlabel" id="stat-area-label">Area</div><div class="statpct" id="stat-area">–</div></div>
      <div class="statbox"><div class="statlabel">This map</div><div class="statpct" id="stat-sheet">–</div></div>
    </div>
    <div id="bar">
      <select id="district"></select>
      <select id="village"></select>
      <div class="row">
        <input id="searchq" placeholder="Search any place to find it…"/>
        <button id="searchbtn">Find</button>
      </div>
    </div>
    <div id="results"></div>
    <div id="step">
      <div id="stepttl">Step 1 — Align the drawing</div>
      <div class="hint" style="padding:4px 0">Drag the drawing to reposition it. Drag the red square handle (bottom-right) or scroll on the drawing to resize it. Switch to Streets basemap (top-right) if that helps.</div>
      <input type="range" id="opacity" min="0" max="100" value="55"/> opacity
      <button id="stepbtn">Confirm alignment →</button>
      <button id="warpbtn" style="width:100%;margin-top:4px;display:none">↔ Stretch corners</button>
    </div>
    <div id="status">Loading…</div>
    <div id="list" class="dim"></div>
    <div id="actions">
      <button id="save">Save</button>
    </div>
  </div>
  <div id="map"></div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const sat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  {maxZoom:21, maxNativeZoom:19, attribution:'Esri World Imagery'});
const labels = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
  {maxZoom:21, maxNativeZoom:19, pane:'shadowPane'});
const streets = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom:19, attribution:'© OpenStreetMap'});
// zoomAnimation off keeps the custom warp overlay correctly registered while zooming
const map = L.map('map', {layers:[sat, labels], zoomAnimation:false}).setView([54.4669, -1.4952], 16);
L.control.layers({'Satellite':sat, 'Streets':streets}, {'Place names':labels}).addTo(map);

let sheet=null, houses=[], placements={}, selected=null, imgSize=null, markers={}, mode='align';

// ---- 4-point homography (src -> dst) ----
function solve(A, b){
  const n=b.length;
  for(let i=0;i<n;i++){
    let p=i; for(let r=i+1;r<n;r++) if(Math.abs(A[r][i])>Math.abs(A[p][i])) p=r;
    [A[i],A[p]]=[A[p],A[i]]; [b[i],b[p]]=[b[p],b[i]];
    for(let r=0;r<n;r++){ if(r===i) continue; const f=A[r][i]/A[i][i];
      for(let c=i;c<n;c++) A[r][c]-=f*A[i][c]; b[r]-=f*b[i]; }
  }
  return b.map((v,i)=>v/A[i][i]);
}
function homography(src, dst){
  const A=[], b=[];
  for(let i=0;i<4;i++){ const [x,y]=src[i], [X,Y]=dst[i];
    A.push([x,y,1,0,0,0,-X*x,-X*y]); b.push(X);
    A.push([0,0,0,x,y,1,-Y*x,-Y*y]); b.push(Y);
  }
  const h=solve(A,b); h.push(1); return h;
}
function applyH(h, x, y){ const d=h[6]*x+h[7]*y+h[8];
  return [(h[0]*x+h[1]*y+h[2])/d, (h[3]*x+h[4]*y+h[5])/d]; }
function matrix3d(h){
  return `matrix3d(${h[0]},${h[3]},0,${h[6]}, ${h[1]},${h[4]},0,${h[7]}, 0,0,1,0, ${h[2]},${h[5]},0,1)`;
}

// ---- image overlay ----
// Normal mode: center (LatLng) + mpp (metres per pixel); corners() derives axis-aligned rect.
// Stretch mode: warpCorners holds 4 independent lat/lng corners; corners() returns those directly.
const warp = {
  img: null, handle: null, warpHandles: [], warpCorners: null,
  center: null,   // L.LatLng — geographic centre of the drawing
  mpp: 0,         // metres per image pixel

  corners(){
    if(this.warpCorners) return this.warpCorners;
    if(!this.center || !imgSize || !this.mpp) return [];
    const lat=this.center.lat, lng=this.center.lng;
    const hw=imgSize.w*this.mpp/2, hh=imgSize.h*this.mpp/2;
    const dLat=hh/111320;
    const dLng=hw/(111320*Math.cos(lat*Math.PI/180));
    return [
      L.latLng(lat+dLat, lng-dLng),  // TL (NW)
      L.latLng(lat+dLat, lng+dLng),  // TR (NE)
      L.latLng(lat-dLat, lng+dLng),  // BR (SE)
      L.latLng(lat-dLat, lng-dLng),  // BL (SW)
    ];
  },

  imgCorners(){ return [[0,0],[imgSize.w,0],[imgSize.w,imgSize.h],[0,imgSize.h]]; },

  create(url){
    this.remove();
    this.img=L.DomUtil.create('img','',map.getPanes().overlayPane);
    this.img.id='warpimg';
    this.img.onload=()=>{ if(!this.center) this.fitToView(); this.render(); setOpacity(); applyMode(); };
    this.img.src=url;
    this.img.addEventListener('mousedown', ev=>this._startBodyDrag(ev));
    this.img.addEventListener('wheel', ev=>{
      if(mode!=='align') return;
      ev.preventDefault();
      const f=ev.deltaY>0?0.95:1/0.95;
      if(this.warpCorners){
        const c=this.center;
        this.warpCorners=this.warpCorners.map(ll=>L.latLng(c.lat+(ll.lat-c.lat)*f, c.lng+(ll.lng-c.lng)*f));
        this.mpp=Math.max(0.05,Math.min(50,this.mpp*f));
        this.render(); this.refreshHandle();
      } else {
        this.mpp=Math.max(0.05,Math.min(50,this.mpp*f));
        this.render(); this.refreshHandle();
      }
    }, {passive:false});
  },

  remove(){ this.clearHandle(); if(this.img){ L.DomUtil.remove(this.img); this.img=null; } },

  fitToView(){
    const b=map.getBounds().pad(-0.25);
    this.center=b.getCenter();
    this.mpp=map.distance(b.getNorthWest(), b.getNorthEast())/imgSize.w;
  },

  render(){
    if(!this.img || !this.center) return;
    const cs=this.corners(); if(cs.length!==4) return;
    const dst=cs.map(ll=>{ const p=map.latLngToLayerPoint(ll); return [p.x,p.y]; });
    this.img.style.transform=matrix3d(homography(this.imgCorners(), dst));
  },

  buildHandle(){
    this.clearHandle();
    if(!this.img) return;
    if(this.warpCorners){
      this.warpHandles=this.warpCorners.map((ll,i)=>{
        const icon=L.divIcon({className:'warphandle',iconSize:[14,14],iconAnchor:[7,7]});
        const m=L.marker(ll,{icon,draggable:true,zIndexOffset:1000,keyboard:false}).addTo(map);
        m.on('drag',()=>{
          this.warpCorners[i]=m.getLatLng();
          this.center=L.latLng(this.warpCorners.reduce((s,c)=>s+c.lat,0)/4,
                               this.warpCorners.reduce((s,c)=>s+c.lng,0)/4);
          this.render();
        });
        return m;
      });
    } else {
      const cs=this.corners(); if(!cs.length) return;
      const icon=L.divIcon({className:'scalehandle',iconSize:[20,20],iconAnchor:[10,10]});
      const m=L.marker(cs[2],{icon,draggable:true,zIndexOffset:1000,keyboard:false}).addTo(map);
      m.on('drag',()=>{
        const distM=map.distance(this.center,m.getLatLng());
        const naturalHalfDiag=Math.sqrt((imgSize.w/2)**2+(imgSize.h/2)**2);
        this.mpp=Math.max(0.05,Math.min(50,distM/naturalHalfDiag));
        this.render(); this.refreshHandle();
      });
      this.handle=m;
    }
  },

  refreshHandle(){
    if(this.warpCorners){
      this.warpHandles.forEach((m,i)=>m.setLatLng(this.warpCorners[i]));
    } else {
      if(this.handle){ const cs=this.corners(); if(cs.length) this.handle.setLatLng(cs[2]); }
    }
  },
  clearHandle(){
    if(this.handle){ map.removeLayer(this.handle); this.handle=null; }
    this.warpHandles.forEach(m=>map.removeLayer(m));
    this.warpHandles=[];
  },

  _startBodyDrag(ev){
    if(mode!=='align') return;
    ev.preventDefault(); map.dragging.disable();
    let last=map.mouseEventToLatLng(ev);
    const move=ev2=>{ const ll=map.mouseEventToLatLng(ev2);
      const dLat=ll.lat-last.lat, dLng=ll.lng-last.lng;
      if(this.warpCorners){
        this.warpCorners=this.warpCorners.map(c=>L.latLng(c.lat+dLat,c.lng+dLng));
        this.center=L.latLng(this.center.lat+dLat,this.center.lng+dLng);
      } else {
        this.center=L.latLng(this.center.lat+dLat,this.center.lng+dLng);
      }
      last=ll; this.render(); this.refreshHandle(); };
    const up=()=>{ document.removeEventListener('mousemove',move); document.removeEventListener('mouseup',up);
      map.dragging.enable(); };
    document.addEventListener('mousemove',move); document.addEventListener('mouseup',up);
  },

  geoToImage(lat,lng){ const cs=this.corners();
    return applyH(homography(cs.map(c=>[c.lng,c.lat]), this.imgCorners()), lng, lat); },

  fromCorners(cs){
    this.center=L.latLng(cs.reduce((s,c)=>s+c.lat,0)/4, cs.reduce((s,c)=>s+c.lng,0)/4);
    this.mpp=map.distance(cs[0],cs[1])/imgSize.w;
  },
};
map.on('move zoom viewreset zoomend moveend', ()=>warp.render());

function setOpacity(){ if(warp.img) warp.img.style.opacity=document.getElementById('opacity').value/100; }
document.getElementById('opacity').oninput=setOpacity;

function applyMode(){
  const al = mode==='align';
  document.getElementById('stepttl').textContent = al ? 'Step 1 — Align the drawing' : 'Step 2 — Click each house';
  document.getElementById('stepbtn').textContent = al ? 'Confirm alignment →' : '← Re-align drawing';
  document.getElementById('list').classList.toggle('dim', al);
  const wb=document.getElementById('warpbtn');
  wb.style.display=al?'block':'none';
  wb.textContent=warp.warpCorners?'🔒 Lock to rectangle':'↔ Stretch corners';
  wb.classList.toggle('active',!!warp.warpCorners);
  if(!warp.img) return;
  warp.img.style.pointerEvents = al ? 'auto' : 'none';
  warp.img.style.cursor = al ? 'move' : 'default';
  if(al) warp.buildHandle(); else warp.clearHandle();
}
document.getElementById('stepbtn').onclick=()=>{ mode = mode==='align'?'place':'align'; applyMode();
  if(mode==='place') selectFirstUnplaced(); };
function toggleWarp(){
  if(warp.warpCorners){
    warp.fromCorners(warp.warpCorners);
    warp.warpCorners=null;
  } else {
    const cs=warp.corners(); if(cs.length!==4) return;
    warp.warpCorners=cs.map(c=>L.latLng(c.lat,c.lng));
  }
  warp.buildHandle();
  const wb=document.getElementById('warpbtn');
  wb.textContent=warp.warpCorners?'🔒 Lock to rectangle':'↔ Stretch corners';
  wb.classList.toggle('active',!!warp.warpCorners);
}
document.getElementById('warpbtn').onclick=toggleWarp;

let allSheets=[];
// Highest-value districts first, per the placement priority order — the rest fall
// back to alphabetical. Update this if her priorities change.
const DISTRICT_PRIORITY=['Wensleydale','Swaledale and Arkengarthdale','Hambleton (West)'];

async function loadVillages(){
  allSheets=await (await fetch('/api/sheets')).json();
  const dsel=document.getElementById('district');
  const districts=[...new Set(allSheets.map(s=>s.district))].sort((a,b)=>{
    const pa=DISTRICT_PRIORITY.indexOf(a), pb=DISTRICT_PRIORITY.indexOf(b);
    if(pa!==-1||pb!==-1) return (pa===-1?999:pa)-(pb===-1?999:pb);
    return a.localeCompare(b);
  });
  dsel.innerHTML=districts.map(d=>{
    const n=allSheets.filter(s=>s.district===d).length;
    return `<option value="${d}">${d} (${n})</option>`;
  }).join('');
  const savedDistrict=localStorage.getItem('whereabouts_district');
  if(savedDistrict && districts.includes(savedDistrict)) dsel.value=savedDistrict;
  dsel.onchange=()=>{ localStorage.setItem('whereabouts_district', dsel.value); populateVillages(); };
  updateOverallStat();
  populateVillages();
}

function populateVillages(){
  const district=document.getElementById('district').value;
  const filtered=allSheets.filter(s=>s.district===district);
  const sel=document.getElementById('village');
  sel.innerHTML=filtered.map(v=>`<option value="${v.id}">${v.village_name} — ${v.placed}/${v.total}</option>`).join('');
  sel.onchange=()=>{ localStorage.setItem('whereabouts_village::'+district, sel.value); loadSheet(sel.value); };
  const savedVillage=localStorage.getItem('whereabouts_village::'+district);
  const target=(savedVillage && filtered.some(v=>v.id===savedVillage)) ? savedVillage : (filtered[0] && filtered[0].id);
  updateAreaStat();
  if(target){ sel.value=target; loadSheet(target); }
}

// Percent-complete stats. Overall/area reflect the last *saved* state (allSheets is
// only refreshed on Save, matching the dropdown option text) — the current-map stat
// is live, counting in-progress unsaved placements too, since that's the figure
// that's actually useful while you're mid-village.
function pct(placed,total){ return total ? Math.round(100*placed/total)+'%' : '–'; }

function updateOverallStat(){
  let placed=0, total=0;
  for(const s of allSheets){ placed+=s.placed; total+=s.total; }
  document.getElementById('stat-overall').textContent=pct(placed,total);
}

function updateAreaStat(){
  const district=document.getElementById('district').value;
  document.getElementById('stat-area-label').textContent=district||'Area';
  let placed=0, total=0;
  for(const s of allSheets){ if(s.district===district){ placed+=s.placed; total+=s.total; } }
  document.getElementById('stat-area').textContent=pct(placed,total);
}

function updateSheetStat(){
  document.getElementById('stat-sheet').textContent=pct(Object.keys(placements).length, houses.length);
}

async function loadSheet(id){
  const d=await (await fetch('/api/sheet/'+id)).json();
  sheet=d.sheet; houses=d.houses; placements=d.placements||{}; imgSize=sheet.image_size;
  Object.values(markers).forEach(m=>map.removeLayer(m)); markers={};
  document.getElementById('results').innerHTML='';
  if(d.alignment&&d.alignment.center) map.setView(d.alignment.center, d.alignment.zoom||16);
  else if(d.centroid) map.setView([d.centroid.lat, d.centroid.lng], 16);
  warp.center=null; warp.mpp=0; warp.warpCorners=null;
  if(d.alignment){
    if(d.alignment.mpp && d.alignment.center){
      warp.center=L.latLng(d.alignment.center[0], d.alignment.center[1]);
      warp.mpp=d.alignment.mpp;
    } else if(d.alignment.corners){
      warp.fromCorners(d.alignment.corners.map(c=>L.latLng(c[0],c[1])));
    }
    if(d.alignment.stretched && d.alignment.corners){
      warp.warpCorners=d.alignment.corners.map(c=>L.latLng(c[0],c[1]));
    }
  }
  warp.create('/images/'+sheet.image_path.split('/').pop());
  for(const [hid,p] of Object.entries(placements)) addMarker(hid,p.lat,p.lng);
  mode = (d.alignment&&d.alignment.corners) ? 'place' : 'align';
  renderList(); selectFirstUnplaced(); applyMode();
}

// ---- place search ----
async function runSearch(){
  const q=document.getElementById('searchq').value.trim(); if(!q) return;
  const res=document.getElementById('results'); res.innerHTML='<div>Searching…</div>';
  try{ const r=await (await fetch('/api/search?q='+encodeURIComponent(q))).json();
    res.innerHTML = r.length ? r.map((p,i)=>`<div data-i="${i}">${p.name}</div>`).join('') : '<div>No results</div>';
    res.querySelectorAll('div[data-i]').forEach(dv=>dv.onclick=()=>{ const p=r[+dv.dataset.i];
      map.setView([p.lat,p.lng],17); res.innerHTML='';
      if(warp.center && mode==='align'){ warp.center=L.latLng(p.lat,p.lng); warp.render(); warp.refreshHandle(); } });
  }catch(e){ res.innerHTML='<div>Search failed</div>'; }
}
document.getElementById('searchbtn').onclick=runSearch;
document.getElementById('searchq').addEventListener('keydown',e=>{ if(e.key==='Enter') runSearch(); });

function renderList(){
  const el=document.getElementById('list');
  el.innerHTML=houses.map(h=>{
    const done=placements[h.id]?'done':''; const sel=selected===h.id?'sel':'';
    const tick=placements[h.id]?'<span class="tick">✓</span>':'';
    const alias=h.names.slice(1).join(' · ');
    return `<div class="house ${done} ${sel}" data-id="${h.id}">
      <span class="num">${h.map_number}</span>${h.names[0]||'(unnamed)'}${tick}
      ${alias?`<div class="alias">${alias}</div>`:''}</div>`;
  }).join('');
  el.querySelectorAll('.house').forEach(dv=>dv.onclick=()=>select(dv.dataset.id));
  const placed=Object.keys(placements).length;
  document.getElementById('status').textContent=`${sheet.village_name}: ${placed}/${houses.length} placed`;
  updateSheetStat();
}
function select(id){ selected=id; renderList();
  const e=document.querySelector('.house.sel'); if(e) e.scrollIntoView({block:'nearest'}); }
function selectFirstUnplaced(){ const h=houses.find(h=>!placements[h.id]); selected=h?h.id:null; renderList(); }
function nextUnplaced(){ const i=houses.findIndex(h=>h.id===selected);
  for(let k=1;k<=houses.length;k++){ const h=houses[(i+k)%houses.length]; if(!placements[h.id]){select(h.id);return;} } }

function addMarker(hid,lat,lng){
  if(markers[hid]) map.removeLayer(markers[hid]);
  const h=houses.find(x=>x.id===hid);
  markers[hid]=L.marker([lat,lng],{title:(h&&h.names[0])||hid}).addTo(map)
    .bindTooltip((h&&h.names[0])||hid);
}

map.on('click', e=>{
  if(mode!=='place' || !selected) return;          // place houses only in step 2
  const [ix,iy]=warp.geoToImage(e.latlng.lat,e.latlng.lng);
  placements[selected]={lat:+e.latlng.lat.toFixed(7), lng:+e.latlng.lng.toFixed(7),
                        image_x:Math.round(ix), image_y:Math.round(iy)};
  addMarker(selected,e.latlng.lat,e.latlng.lng);
  renderList(); nextUnplaced();
});

document.getElementById('save').onclick=save;
async function save(){
  const c=map.getCenter();
  const body={alignment:{corners:warp.corners().map(p=>[p.lat,p.lng]),
              center:[warp.center.lat,warp.center.lng], mpp:warp.mpp,
              zoom:map.getZoom(), stretched:!!warp.warpCorners}, houses:placements};
  const r=await (await fetch('/api/sheet/'+sheet.id,{method:'POST',headers:{'Content-Type':'application/json'},
              body:JSON.stringify(body)})).json();
  document.getElementById('status').textContent=`Saved ${r.placed} houses ✓`;
  const o=document.getElementById('village').querySelector(`option[value="${sheet.id}"]`);
  if(o) o.textContent=`${sheet.village_name} — ${r.placed}/${houses.length}`;
  const cached=allSheets.find(s=>s.id===sheet.id);
  if(cached) cached.placed=r.placed;
  updateOverallStat(); updateAreaStat();
}
document.onkeydown=e=>{ if(e.target.tagName==='SELECT'||e.target.tagName==='INPUT') return;
  if(e.key==='n'||e.key==='N') nextUnplaced();
  if(e.key==='u'||e.key==='U'){ if(selected&&placements[selected]){ map.removeLayer(markers[selected]);
    delete markers[selected]; delete placements[selected]; renderList(); } }
};
loadVillages();
</script>
</body>
</html>"""

_APP_PAGE = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Whereabouts</title>
<script src="https://cdn.jsdelivr.net/npm/fuse.js@7.0.0/dist/fuse.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;height:100vh;display:flex;flex-direction:column;background:#f4f4f4}
header{background:#1b3d5f;color:#fff;padding:12px 16px;flex:none}
header h1{font-size:17px;font-weight:600;letter-spacing:.3px}
header p{font-size:11px;opacity:.65;margin-top:2px}
#search-wrap{padding:10px 14px;background:#fff;border-bottom:1px solid #ddd;flex:none}
#searchq{width:100%;padding:9px 12px;font-size:15px;border:1px solid #ccc;border-radius:8px;outline:none}
#searchq:focus{border-color:#1b3d5f}
#content{flex:1;display:flex;overflow:hidden}
#results{width:280px;flex:none;overflow-y:auto;background:#fff;border-right:1px solid #ddd}
.hint{padding:16px 14px;color:#aaa;font-size:13px}
.result{padding:9px 14px;border-bottom:1px solid #f0f0f0;cursor:pointer}
.result:hover,.result.sel{background:#ebf2ff}
.rname{font-size:14px;font-weight:500;color:#111}
.rvillage{font-size:11px;color:#888;margin-top:2px}
.ralias{font-size:11px;color:#bbb;margin-top:1px}
#detail{flex:1;overflow:auto;display:flex;flex-direction:column;align-items:center;padding:24px 20px;gap:8px}
#detail.empty{justify-content:center;color:#ccc;font-size:14px}
#dname{font-size:20px;font-weight:600;color:#111;text-align:center}
#dvillage{font-size:13px;color:#777}
#img-wrap{position:relative;display:inline-block;max-width:100%;margin-top:8px}
#dimg{max-width:100%;max-height:62vh;display:block;border:1px solid #ddd;border-radius:4px}
#ring{position:absolute;border:4px solid #e00;border-radius:50%;width:44px;height:44px;
  margin:-22px 0 0 -22px;pointer-events:none;box-shadow:0 0 0 2px rgba(255,255,255,.7)}
#navbtn{margin-top:12px;padding:12px 32px;background:#1b3d5f;color:#fff;border:none;
  border-radius:8px;font-size:15px;font-weight:500;cursor:pointer;letter-spacing:.2px}
#navbtn:hover{background:#2a5a8a}
#nocoords{font-size:12px;color:#bbb;margin-top:8px}
</style>
</head>
<body>
<header>
  <h1>Whereabouts</h1>
  <p>Named house finder — North Yorkshire</p>
</header>
<div id="search-wrap">
  <input id="searchq" type="search" placeholder="Search by house name…" autocomplete="off"/>
</div>
<div id="content">
  <div id="results"><div class="hint">Type a name to search</div></div>
  <div id="detail" class="empty">Select a house from the results</div>
</div>
<script>
let fuse=null, allHouses=[], selectedId=null, resizeListener=null;

async function init(){
  allHouses=await (await fetch('/api/app/houses')).json();
  fuse=new Fuse(allHouses,{keys:['names'],threshold:0.35,minMatchCharLength:2,includeScore:true});
  document.getElementById('searchq').addEventListener('input',onSearch);
  document.getElementById('searchq').focus();
}

function onSearch(){
  const q=document.getElementById('searchq').value.trim();
  const res=document.getElementById('results');
  if(!q){res.innerHTML='<div class="hint">Type a name to search</div>';return;}
  const hits=fuse.search(q,{limit:50});
  if(!hits.length){res.innerHTML='<div class="hint">No results</div>';return;}
  hits.sort((a,b)=>{
    const an=/^\d/.test(a.item.names[0]||''), bn=/^\d/.test(b.item.names[0]||'');
    return an===bn ? 0 : an ? 1 : -1;
  });
  res.innerHTML=hits.map(r=>{
    const h=r.item;
    const alias=h.names.slice(1).join(' · ');
    return `<div class="result${selectedId===h.id?' sel':''}" data-id="${h.id}">
      <div class="rname">${h.names[0]||'(unnamed)'}</div>
      <div class="rvillage">${h.village_name||h.sheet_id}</div>
      ${alias?`<div class="ralias">${alias}</div>`:''}
    </div>`;
  }).join('');
  res.querySelectorAll('.result').forEach(el=>el.onclick=()=>{
    selectedId=el.dataset.id;
    res.querySelectorAll('.result').forEach(r=>r.classList.toggle('sel',r===el));
    showDetail(allHouses.find(h=>h.id===selectedId));
  });
}

function showDetail(h){
  if(resizeListener){window.removeEventListener('resize',resizeListener);resizeListener=null;}
  const det=document.getElementById('detail');
  det.className='';
  const hasCoords=h.lat!=null, hasImg=h.image_x!=null;
  const imgFile=h.image_path?h.image_path.split('/').pop():'';
  det.innerHTML=`
    <div id="dname">${h.names.join(' / ')}</div>
    <div id="dvillage">${h.village_name||h.sheet_id}</div>
    <div id="img-wrap">
      <img id="dimg" src="/images/${imgFile}"/>
      ${hasImg?'<div id="ring"></div>':''}
    </div>
    ${hasCoords?`<button id="navbtn" onclick="navigate(${h.lat},${h.lng})">Navigate →</button>`:''}
    ${!hasCoords?'<div id="nocoords">Not yet placed — no coordinates available</div>':''}
  `;
  if(hasImg){
    const img=document.getElementById('dimg');
    const ring=document.getElementById('ring');
    const place=()=>{
      const sx=img.offsetWidth/img.naturalWidth, sy=img.offsetHeight/img.naturalHeight;
      ring.style.left=(h.image_x*sx)+'px'; ring.style.top=(h.image_y*sy)+'px';
    };
    if(img.complete&&img.naturalWidth) place(); else img.onload=place;
    resizeListener=place; window.addEventListener('resize',place);
  }
}

function navigate(lat,lng){
  window.open(`https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}&travelmode=driving`);
}

init();
</script>
</body>
</html>"""
