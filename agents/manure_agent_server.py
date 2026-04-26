

import sys, os, uuid, time, base64, tempfile
# FIX: forcer UTF-8 sur stdout avant tout import FastAPI/uvicorn
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

from tools.tool_manure import analyze_manure
from tools.tool_adaptive import adaptive_intelligence

PORT = int(os.getenv("MANURE_AGENT_PORT", 8002))
AGENT_ID = "manure-analysis-agent"
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

app = FastAPI(title="Manure Analysis Agent", version=AGENT_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Agent Card ─────────────────────────────────────────────────

AGENT_CARD = {
    "id": AGENT_ID,
    "name": "Manure Analysis Agent",
    "version": AGENT_VERSION,
    "description": (
        "Specialized agent for poultry manure (fientes) analysis at Elmazraa plant. "
        "Analyzes RGB and microscopic images of manure samples using VLM. "
        "Returns structured biological/agronomic interpretation, confidence scores, "
        "nitrogen content estimate, and valorization potential."
    ),
    "capabilities": [
        "manure_rgb_analysis",
        "manure_microscopic_analysis",
        "biological_interpretation",
        "nitrogen_content_estimation",
        "valorization_potential_assessment",
        "adaptive_quality_evaluation",
    ],
    "input_schema": {
        "type": "object",
        "properties": {
            "manure_rgb_b64":   {"type": "string"},
            "manure_micro_b64": {"type": "string"},
            "manure_rgb_path":  {"type": "string"},
            "manure_micro_path":{"type": "string"},
            "user_request":     {"type": "string"},
            "session_id":       {"type": "string"},
        },
        "required": [],
    },
    "endpoint": f"http://localhost:{PORT}/analyze",
    "health_endpoint": f"http://localhost:{PORT}/health",
    "card_endpoint": f"http://localhost:{PORT}/.well-known/agent.json",
    "protocol": "A2A/1.0",
}


# ── Request / Response models ──────────────────────────────────

class ManureAnalysisRequest(BaseModel):
    manure_rgb_b64:    Optional[str] = None
    manure_micro_b64:  Optional[str] = None
    manure_rgb_path:   Optional[str] = None
    manure_micro_path: Optional[str] = None
    user_request:      Optional[str] = None
    session_id:        Optional[str] = None


class ManureAnalysisResponse(BaseModel):
    session_id:             str
    agent_id:               str
    status:                 str
    manure_description:     Optional[str] = None
    confidence:             Optional[float] = None
    adaptive_result:        Optional[str] = None
    key_indicators:         list = []
    nitrogen_level:         Optional[str] = None
    valorization_potential: Optional[str] = None
    treatment_urgency:      Optional[str] = None
    error:                  Optional[str] = None
    processing_time_s:      Optional[float] = None


# ── Utilities ──────────────────────────────────────────────────

def _save_b64_to_tmp(b64_str: str, suffix: str = ".png") -> str:
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


def _extract_nitrogen_level(text: str) -> str:
    import re
    m = re.search(r'level[:\s]*(\w+).*?(?:nitrogen|azote)', text.lower())
    if not m:
        m = re.search(r'nitrogen[^\n]*level[:\s]*(\w+)', text.lower())
    if m:
        return m.group(1)
    if re.search(r'urate.*abundant|abundant.*urate', text.lower()):
        return "high"
    return "medium"


def _extract_valorization_potential(text: str) -> str:
    import re
    m = re.search(r'suitable_for[:\s]*([^\n]+)', text.lower())
    if m:
        return m.group(1).strip()[:80]
    return "compost/biostimulant"


def _extract_key_indicators(text: str) -> list:
    import re
    if not text:
        return []
    indicators = []
    patterns = [
        (r'urate[_\s]crystals[:\s]*(\w+)',               "urate_crystals"),
        (r'egg.like[_\s]structures[:\s]*(\w+)',           "egg_like_structures"),
        (r'bacterial[_\s]clusters[:\s]*(\w+)',            "bacterial_clusters"),
        (r'hyphae[_\s]filaments[:\s]*(\w+)',              "hyphae_filaments"),
        (r'spores[:\s]*(\w+)',                            "spores"),
        (r'parasite[_\s]like[_\s]structures[:\s]*(\w+)', "parasite_structures"),
        (r'plant[_\s]fibers[:\s]*(\w+)',                  "plant_fibers"),
        (r'overall[_\s]density[:\s]*(\w+)',               "density"),
        (r'dominant[_\s]element[:\s]*(\w+)',              "dominant_element"),
        (r'mold[_\s]patches[:\s]*(\w+)',                  "mold"),
        (r'health[_\s]status[:\s]*(\w+)',                 "health_status"),
    ]
    text_lower = text.lower()
    for pattern, label in patterns:
        m = re.search(pattern, text_lower)
        if m:
            val = m.group(1).strip()
            if val not in ("not_visible", "absent", "none", "no", "not_determinable", "unclear"):
                indicators.append(f"{label}:{val}")
    return indicators


def _extract_treatment_urgency(text: str) -> str:
    import re
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


@app.post("/analyze", response_model=ManureAnalysisResponse)
async def analyze(request: ManureAnalysisRequest):
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

        # Decode base64 images to temp files (preferred)
        if request.manure_rgb_b64:
            try:
                rgb_path = _save_b64_to_tmp(request.manure_rgb_b64, ".png")
                tmp_files.append(rgb_path)
                _safe_print(f"[ManureAgent] Decoded RGB b64 to temp file")
            except Exception as e:
                err = str(e).encode('ascii', errors='replace').decode('ascii')
                _safe_print(f"[ManureAgent] b64 decode error (RGB): {err}")

        if request.manure_micro_b64:
            try:
                micro_path = _save_b64_to_tmp(request.manure_micro_b64, ".png")
                tmp_files.append(micro_path)
                _safe_print(f"[ManureAgent] Decoded MICRO b64 to temp file")
            except Exception as e:
                err = str(e).encode('ascii', errors='replace').decode('ascii')
                _safe_print(f"[ManureAgent] b64 decode error (MICRO): {err}")

        # Fallback: direct paths
        if not rgb_path and request.manure_rgb_path:
            rgb_path = (request.manure_rgb_path or "").strip()

        if not micro_path and request.manure_micro_path:
            micro_path = (request.manure_micro_path or "").strip()

        if not rgb_path and not micro_path:
            return ManureAnalysisResponse(
                session_id=session_id,
                agent_id=AGENT_ID,
                status="error",
                error="No image provided",
                processing_time_s=round(time.time() - t0, 2),
            )

        _safe_print(f"[ManureAgent] Session {session_id} — analyzing rgb={bool(rgb_path)} micro={bool(micro_path)}")

        manure_result = analyze_manure.invoke({
            "manure_rgb":   rgb_path   or "",
            "manure_micro": micro_path or "",
        })

        if not manure_result or len(manure_result.strip()) < 20:
            return ManureAnalysisResponse(
                session_id=session_id,
                agent_id=AGENT_ID,
                status="error",
                error="VLM analysis returned empty result",
                processing_time_s=round(time.time() - t0, 2),
            )

        # Run adaptive_intelligence
        try:
            adaptive_result = adaptive_intelligence.invoke({
                "water_description":  "",
                "manure_description": manure_result,
                "questions_asked":    "",
                "context_so_far":     "",
                "user_request":       request.user_request or "",
            })
        except Exception as e:
            err = str(e).encode('ascii', errors='replace').decode('ascii')
            _safe_print(f"[ManureAgent] adaptive_intelligence error (non-fatal): {err}")
            adaptive_result = "ACTION: CONTINUE\nCONFIDENCE_SCORE: 0.55"

        confidence             = _extract_confidence(manure_result)
        key_indicators         = _extract_key_indicators(manure_result)
        nitrogen_level         = _extract_nitrogen_level(manure_result)
        valorization_potential = _extract_valorization_potential(manure_result)
        treatment_urgency      = _extract_treatment_urgency(manure_result)

        _safe_print(f"[ManureAgent] Session {session_id} done. conf={confidence:.2f} N={nitrogen_level}")

        return ManureAnalysisResponse(
            session_id=session_id,
            agent_id=AGENT_ID,
            status="success",
            manure_description=manure_result,
            confidence=confidence,
            adaptive_result=adaptive_result,
            key_indicators=key_indicators,
            nitrogen_level=nitrogen_level,
            valorization_potential=valorization_potential,
            treatment_urgency=treatment_urgency,
            processing_time_s=round(time.time() - t0, 2),
        )

    except Exception as e:
        err = str(e).encode('ascii', errors='replace').decode('ascii')
        _safe_print(f"[ManureAgent] ERROR session {session_id}: {err}")
        return ManureAnalysisResponse(
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
    _safe_print(f"[ManureAgent] Starting on port {PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")