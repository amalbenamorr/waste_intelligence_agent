

import sys, os, uuid, time, base64, tempfile
# FIX: forcer UTF-8 sur stdout avant tout import FastAPI/uvicorn
# Cela évite les erreurs charmap sur les terminaux Windows cp1252
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ('utf-8', 'utf8'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
from pathlib import Path
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import uvicorn

from tools.tool_water import analyze_water
from tools.tool_adaptive import adaptive_intelligence

PORT = int(os.getenv("WATER_AGENT_PORT", 8001))
AGENT_ID = "water-analysis-agent"
AGENT_VERSION = "1.0.0"


# ── Safe print ─────────────────────────────────────────────────

def _safe_print(msg: str):
    """Print safely regardless of terminal encoding."""
    try:
        print(msg)
    except (UnicodeEncodeError, UnicodeDecodeError):
        encoding = getattr(sys.stdout, 'encoding', None) or 'utf-8'
        safe = str(msg).encode(encoding, errors='replace').decode(encoding, errors='replace')
        print(safe)


# ── FastAPI app ────────────────────────────────────────────────

app = FastAPI(title="Water Analysis Agent", version=AGENT_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Agent Card ─────────────────────────────────────────────────

AGENT_CARD = {
    "id": AGENT_ID,
    "name": "Water Analysis Agent",
    "version": AGENT_VERSION,
    "description": (
        "Specialized agent for poultry wastewater analysis at Elmazraa plant. "
        "Analyzes RGB and microscopic images of wastewater samples using VLM. "
        "Returns structured biological interpretation, confidence scores, "
        "and treatment urgency assessment."
    ),
    "capabilities": [
        "wastewater_rgb_analysis",
        "wastewater_microscopic_analysis",
        "biological_interpretation",
        "treatment_urgency_assessment",
        "adaptive_quality_evaluation",
    ],
    "input_schema": {
        "type": "object",
        "properties": {
            "water_rgb_b64":   {"type": "string"},
            "water_micro_b64": {"type": "string"},
            "water_rgb_path":  {"type": "string"},
            "water_micro_path":{"type": "string"},
            "user_request":    {"type": "string"},
            "session_id":      {"type": "string"},
        },
        "required": [],
    },
    "endpoint": f"http://localhost:{PORT}/analyze",
    "health_endpoint": f"http://localhost:{PORT}/health",
    "card_endpoint": f"http://localhost:{PORT}/.well-known/agent.json",
    "protocol": "A2A/1.0",
}


# ── Request / Response models ──────────────────────────────────

class WaterAnalysisRequest(BaseModel):
    water_rgb_b64:    Optional[str] = None
    water_micro_b64:  Optional[str] = None
    water_rgb_path:   Optional[str] = None
    water_micro_path: Optional[str] = None
    user_request:     Optional[str] = None
    session_id:       Optional[str] = None


class WaterAnalysisResponse(BaseModel):
    session_id:        str
    agent_id:          str
    status:            str
    water_description: Optional[str] = None
    confidence:        Optional[float] = None
    adaptive_result:   Optional[str] = None
    key_indicators:    list = []
    treatment_urgency: Optional[str] = None
    error:             Optional[str] = None
    processing_time_s: Optional[float] = None


# ── Utilities ──────────────────────────────────────────────────

def _save_b64_to_tmp(b64_str: str, suffix: str = ".png") -> str:
    """Decode base64 image and save to temp file. Returns path."""
    data = base64.b64decode(b64_str)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(data)
    tmp.flush()
    tmp.close()
    return tmp.name


def _extract_confidence(text: str) -> float:
    import re
    if not text:
        return 0.0
    for m in re.findall(r'(?:global|confidence)[:\s=]+([0-9.]+)', text, re.IGNORECASE):
        try:
            v = float(m)
            if 0.0 <= v <= 1.0:
                return v
        except ValueError:
            pass
    t = text.lower()
    if "confidence: high"   in t: return 0.85
    if "confidence: medium" in t: return 0.65
    if "confidence: low"    in t: return 0.35
    return 0.55


def _extract_key_indicators(text: str) -> list:
    import re
    if not text:
        return []
    indicators = []
    patterns = [
        (r'urate[_\s]crystals[:\s]*(\w+)',          "urate_crystals"),
        (r'cell_count_estimate[:\s]*(\w+)',          "cell_count"),
        (r'turbidity[_\s]level[:\s]*(\w+)',          "turbidity"),
        (r'biofilm_presence[:\s]*(\w+)',             "biofilm"),
        (r'fat_globules[:\s]*(\w+)',                 "fat_globules"),
        (r'parasite[_\s]like[_\s]structures[:\s]*(\w+)', "parasite_structures"),
        (r'fungal_elements[:\s]*(\w+)',              "fungal_elements"),
        (r'dominant[:\s]*(\w+)',                     "color"),
    ]
    text_lower = text.lower()
    for pattern, label in patterns:
        m = re.search(pattern, text_lower)
        if m:
            val = m.group(1).strip()
            if val not in ("not_visible", "absent", "none", "no", "not_determinable"):
                indicators.append(f"{label}:{val}")
    return indicators


def _extract_treatment_urgency(text: str) -> str:
    import re
    if not text:
        return "standard"
    m = re.search(r'urgency[:\s]*(\w+)', text.lower())
    if m:
        return m.group(1)
    if "urgent" in text.lower() or "critical" in text.lower():
        return "urgent"
    return "standard"


# ── Endpoints ──────────────────────────────────────────────────

@app.get("/.well-known/agent.json")
async def get_agent_card():
    return AGENT_CARD


@app.get("/health")
async def health():
    return {
        "agent_id": AGENT_ID,
        "status": "healthy",
        "version": AGENT_VERSION,
        "port": PORT,
        "timestamp": time.time(),
    }


@app.post("/analyze", response_model=WaterAnalysisResponse)
async def analyze(request: WaterAnalysisRequest):
    """
    Main A2A analysis endpoint.
    Accepts base64 images (preferred) or file paths.
    """
    t0 = time.time()
    session_id = request.session_id or str(uuid.uuid4())[:8]
    tmp_files = []

    try:
        rgb_path   = ""
        micro_path = ""

        # Decode base64 images to temp files (preferred path)
        if request.water_rgb_b64:
            try:
                rgb_path = _save_b64_to_tmp(request.water_rgb_b64, ".png")
                tmp_files.append(rgb_path)
                _safe_print(f"[WaterAgent] Decoded RGB b64 to temp file")
            except Exception as e:
                err = str(e).encode('ascii', errors='replace').decode('ascii')
                _safe_print(f"[WaterAgent] b64 decode error (RGB): {err}")

        if request.water_micro_b64:
            try:
                micro_path = _save_b64_to_tmp(request.water_micro_b64, ".png")
                tmp_files.append(micro_path)
                _safe_print(f"[WaterAgent] Decoded MICRO b64 to temp file")
            except Exception as e:
                err = str(e).encode('ascii', errors='replace').decode('ascii')
                _safe_print(f"[WaterAgent] b64 decode error (MICRO): {err}")

        # Fallback: direct paths (only if no b64 provided)
        if not rgb_path and request.water_rgb_path:
            rgb_path = (request.water_rgb_path or "").strip()

        if not micro_path and request.water_micro_path:
            micro_path = (request.water_micro_path or "").strip()

        if not rgb_path and not micro_path:
            return WaterAnalysisResponse(
                session_id=session_id,
                agent_id=AGENT_ID,
                status="error",
                error="No image provided (water_rgb_b64, water_micro_b64, water_rgb_path, or water_micro_path required)",
                processing_time_s=round(time.time() - t0, 2),
            )

        _safe_print(f"[WaterAgent] Session {session_id} — analyzing rgb={bool(rgb_path)} micro={bool(micro_path)}")

        water_result = analyze_water.invoke({
            "water_rgb":   rgb_path   or None,
            "water_micro": micro_path or None,
        })

        if not water_result or len(water_result.strip()) < 20:
            return WaterAnalysisResponse(
                session_id=session_id,
                agent_id=AGENT_ID,
                status="error",
                error="VLM analysis returned empty result",
                processing_time_s=round(time.time() - t0, 2),
            )

        # Run adaptive_intelligence
        try:
            adaptive_result = adaptive_intelligence.invoke({
                "water_description":  water_result,
                "manure_description": "",
                "questions_asked":    "",
                "context_so_far":     "",
                "user_request":       request.user_request or "",
            })
        except Exception as e:
            err = str(e).encode('ascii', errors='replace').decode('ascii')
            _safe_print(f"[WaterAgent] adaptive_intelligence error (non-fatal): {err}")
            adaptive_result = "ACTION: CONTINUE\nCONFIDENCE_SCORE: 0.55"

        confidence        = _extract_confidence(water_result)
        key_indicators    = _extract_key_indicators(water_result)
        treatment_urgency = _extract_treatment_urgency(water_result)

        _safe_print(f"[WaterAgent] Session {session_id} done. conf={confidence:.2f} indicators={len(key_indicators)}")

        return WaterAnalysisResponse(
            session_id=session_id,
            agent_id=AGENT_ID,
            status="success",
            water_description=water_result,
            confidence=confidence,
            adaptive_result=adaptive_result,
            key_indicators=key_indicators,
            treatment_urgency=treatment_urgency,
            processing_time_s=round(time.time() - t0, 2),
        )

    except Exception as e:
        err = str(e).encode('ascii', errors='replace').decode('ascii')
        _safe_print(f"[WaterAgent] ERROR session {session_id}: {err}")
        return WaterAnalysisResponse(
            session_id=session_id,
            agent_id=AGENT_ID,
            status="error",
            error=err,
            processing_time_s=round(time.time() - t0, 2),
        )
    finally:
        for f in tmp_files:
            try:
                os.unlink(f)
            except Exception:
                pass


if __name__ == "__main__":
    _safe_print(f"[WaterAgent] Starting on port {PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")