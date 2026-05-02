# ============================================================
# main_a2a.py — Point d'entrée A2A avec serveur dashboard
#
# Lance le pipeline A2A complet et expose un dashboard HTTP.
# Usage:
#   # 1. Lancer les sous-agents dans des terminaux séparés:
#   python agents/water_agent_server.py
#   python agents/manure_agent_server.py
#
#   # 2. Lancer le main A2A:
#   python main_a2a.py
#
# Ou tout en un via:
#   python start_all.py
# ============================================================

import os, sys, asyncio, uuid, json, time, threading, queue
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
from pathlib import Path
load_dotenv()
from fastapi import File, UploadFile, Form
import shutil

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
import uvicorn

from agents.master_agent_a2a import run_agent_a2a
from agents.a2a_client import check_agents_health, WATER_AGENT_URL, MANURE_AGENT_URL
from fastapi.staticfiles import StaticFiles
from agents.voice_handler import router as voice_router


MASTER_PORT = int(os.getenv("MASTER_PORT", 8000))

app = FastAPI(title="Elmazraa Waste Intelligence — A2A Master", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")
app.include_router(voice_router)

# ── In-memory event store for SSE ─────────────────────────────
# Maps session_id → queue of events
_event_queues: dict[str, queue.Queue] = {}
_session_results: dict[str, dict] = {}
_xai_store: dict[str, dict] = {}


# ── Request model ─────────────────────────────────────────────

class AnalysisRequest(BaseModel):
    water_rgb_path:    Optional[str] = None
    water_micro_path:  Optional[str] = None
    manure_rgb_path:   Optional[str] = None
    manure_micro_path: Optional[str] = None
    user_request:      Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────

@app.get("/health")
async def health():
    agent_health = await check_agents_health([WATER_AGENT_URL, MANURE_AGENT_URL])
    return {
        "master": "healthy",
        "agents": agent_health,
        "timestamp": time.time(),
    }


@app.get("/.well-known/agent.json")
async def master_card():
    """Master Agent Card."""
    return {
        "id": "master-orchestrator",
        "name": "Elmazraa Waste Intelligence Master",
        "version": "1.0.0",
        "description": "A2A orchestrator for poultry waste analysis. Delegates to specialized sub-agents.",
        "capabilities": ["orchestration", "risk_assessment", "valorization", "roi_calculation", "report_generation"],
        "sub_agents": [WATER_AGENT_URL, MANURE_AGENT_URL],
        "protocol": "A2A/1.0",
    }


@app.post("/analyze")
async def analyze(request: AnalysisRequest):
    """
    Launch A2A analysis pipeline.
    Returns session_id immediately — use /events/{session_id} for real-time updates.
    """
    session_id = str(uuid.uuid4())[:8]
    _event_queues[session_id] = queue.Queue()

    def status_callback(event: dict):
        q = _event_queues.get(session_id)
        if q:
            q.put(event)

    def run_in_thread():
        try:
            result = run_agent_a2a(
                water_rgb    = request.water_rgb_path,
                water_micro  = request.water_micro_path,
                manure_rgb   = request.manure_rgb_path,
                manure_micro = request.manure_micro_path,
                user_request = request.user_request,
                status_callback = status_callback,
            )
            _session_results[session_id] = result
            
            # Store XAI data
            _xai_store[session_id] = {
                "session_id": session_id,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "cot_trace": result.get("xai_cot_trace", []),
                "attribution": result.get("xai_attribution", {})
            }
            
            # Save XAI data to file for persistence/download
            xai_file = Path("outputs/xai") / f"xai_{session_id}.json"
            with open(xai_file, "w", encoding="utf-8") as f:
                json.dump(_xai_store[session_id], f, indent=4)
            # Signal completion
            q = _event_queues.get(session_id)
            if q:
                q.put({"type": "done", "data": {"session_id": session_id}})
        except Exception as e:
            q = _event_queues.get(session_id)
            if q:
                q.put({"type": "error", "data": {"error": str(e)}})

    t = threading.Thread(target=run_in_thread, daemon=True)
    t.start()

    return JSONResponse({"session_id": session_id, "events_url": f"/events/{session_id}"})


@app.get("/events/{session_id}")
async def events(session_id: str):
    """Server-Sent Events stream for real-time dashboard updates."""

    async def generator():
        q = _event_queues.get(session_id)
        if not q:
            yield f"data: {json.dumps({'type': 'error', 'data': {'error': 'Session not found'}})}\n\n"
            return

        timeout = 300  # 5 min max
        t0 = time.time()

        while time.time() - t0 < timeout:
            try:
                event = q.get(timeout=1.0)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") in ("done", "error", "session_done"):
                    break
            except queue.Empty:
                # Keepalive ping
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream")


@app.get("/result/{session_id}")
async def get_result(session_id: str):
    """Get final result for a session."""
    result = _session_results.get(session_id)
    if not result:
        return JSONResponse({"error": "Result not ready yet"}, status_code=202)

    # Return safe subset (report can be very long)
    report = result.get("final_report", "")
    if report and "ELMAZRAA WASTE INTELLIGENCE REPORT" in report:
        report_body = report.split("ELMAZRAA WASTE INTELLIGENCE REPORT", 1)[-1]
    else:
        report_body = report

    return JSONResponse({
        "session_id":      result.get("session_id"),
        "waste_type":      result.get("waste_type"),
        "water_confidence":result.get("water_confidence"),
        "manure_confidence":result.get("manure_confidence"),
        "agents_called":   result.get("agents_called"),
        "parallel_time_s": result.get("parallel_time_s"),
        "total_time_s":    result.get("total_time_s"),
        "memory_saved":    result.get("memory_saved"),
        "report_preview":  (report_body or "")[:2000],
        "report_full":     report_body or "",
    })


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the A2A Dashboard."""
    dashboard_path = Path(__file__).parent / "dashboard.html"
    if dashboard_path.exists():
        return HTMLResponse(dashboard_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Dashboard not found — place dashboard.html next to main_a2a.py</h1>")


@app.get("/xai", response_class=HTMLResponse)
async def xai_dashboard():
    """Serve the XAI Dashboard."""
    dashboard_path = Path(__file__).parent / "xai_dashboard.html"
    if dashboard_path.exists():
        return HTMLResponse(dashboard_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>XAI Dashboard not found — check if xai_dashboard.html is placed next to main_a2a.py</h1>")


@app.get("/latest_xai")
async def get_latest_xai():
    """Get XAI data for the most recent session."""
    if not _xai_store:
        return JSONResponse({"error": "No XAI data available yet"}, status_code=404)
    # Get last inserted item
    latest_id = list(_xai_store.keys())[-1]
    return _xai_store[latest_id]


@app.get("/xai/{session_id}")
async def get_xai_session(session_id: str):
    """Get XAI data for a specific session."""
    data = _xai_store.get(session_id)
    if not data:
        # Try to load from file
        xai_file = Path("outputs/xai") / f"xai_{session_id}.json"
        if xai_file.exists():
            return JSONResponse(json.loads(xai_file.read_text(encoding="utf-8")))
        return JSONResponse({"error": "Session not found"}, status_code=404)
    return data

@app.post("/analyze_files")
async def analyze_files(
    water_rgb:         UploadFile = File(default=None),
    water_micro:       UploadFile = File(default=None),
    manure_rgb:        UploadFile = File(default=None),
    manure_micro:      UploadFile = File(default=None),
    water_rgb_path:    str = Form(default=None),
    water_micro_path:  str = Form(default=None),
    manure_rgb_path:   str = Form(default=None),
    manure_micro_path: str = Form(default=None),
    user_request:      str = Form(default=None),
):
    
    import shutil
 
    upload_dir = Path("outputs/uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)
 
    session_id = str(uuid.uuid4())[:8]
 
    def save_upload(uf: UploadFile, name: str):
        if not uf or not uf.filename:
            return None
        suffix = Path(uf.filename).suffix or ".png"
        dest   = upload_dir / f"{session_id}_{name}{suffix}"
        with open(dest, "wb") as f:
            shutil.copyfileobj(uf.file, f)
        return str(dest).replace("\\\\", "/")
 
    wrp = save_upload(water_rgb,    "water_rgb")   or water_rgb_path
    wmp = save_upload(water_micro,  "water_micro") or water_micro_path
    mrp = save_upload(manure_rgb,   "manure_rgb")  or manure_rgb_path
    mmp = save_upload(manure_micro, "manure_micro")or manure_micro_path
 
    if not any([wrp, wmp, mrp, mmp]):
        return JSONResponse(
            {"error": "Aucune image fournie. Fournissez au moins un fichier ou un chemin."},
            status_code=422,
        )
 
    _event_queues[session_id] = queue.Queue()
 
    def _status_cb(event: dict):
        q = _event_queues.get(session_id)
        if q:
            q.put(event)
 
    def _run():
        try:
            result = run_agent_a2a(
                water_rgb    = wrp,
                water_micro  = wmp,
                manure_rgb   = mrp,
                manure_micro = mmp,
                user_request = user_request,
                status_callback = _status_cb,
            )
            _session_results[session_id] = result
            
            # Store XAI data
            _xai_store[session_id] = {
                "session_id": session_id,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "cot_trace": result.get("xai_cot_trace", []),
                "attribution": result.get("xai_attribution", {})
            }
            
            # Save XAI data to file
            xai_file = Path("outputs/xai") / f"xai_{session_id}.json"
            with open(xai_file, "w", encoding="utf-8") as f:
                json.dump(_xai_store[session_id], f, indent=4)
            _event_queues[session_id].put({
                "type": "done",
                "data": {"session_id": session_id},
            })
        except Exception as ex:
            _event_queues[session_id].put({
                "type":  "error",
                "data":  {"error": str(ex)},
            })
 
    t = threading.Thread(target=_run, daemon=True)
    t.start()
 
    return JSONResponse({
        "session_id": session_id,
        "events_url": f"/events/{session_id}",
    })

if __name__ == "__main__":
    print(f"[Master] Starting A2A Dashboard on http://localhost:{MASTER_PORT}")
    print(f"[Master] Sub-agents: water={WATER_AGENT_URL} manure={MANURE_AGENT_URL}")
    uvicorn.run(app, host="0.0.0.0", port=MASTER_PORT, log_level="info")