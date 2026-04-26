import sys, os, uuid, re, asyncio, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
from pathlib import Path
load_dotenv()

from state import AgentState
from memory.long_term import LongTermMemory
from tools.tool_risk         import search_risk
from tools.tool_valorization import search_valorization
from tools.tool_roi          import calculate_roi
from tools.tool_memory       import save_to_memory
from tools.tool_report       import generate_report
from tools.tool_environmental import assess_environmental_impact   # ← NEW
from typing import Optional

from agents.a2a_client import (
    AgentRegistry,
    delegate_analyses_parallel,
    check_agents_health,
    _is_valid_path,
    WATER_AGENT_URL,
    MANURE_AGENT_URL,
)


memory_store = LongTermMemory()

AGENT_URLS = [WATER_AGENT_URL, MANURE_AGENT_URL]


# ── Utilities ─────────────────────────────────────────────────

def _extract_confidence(text: str) -> float:
    if not text: return 0.0
    for m in re.findall(r'(?:global|confidence)[:\s=]+([0-9.]+)', text, re.IGNORECASE):
        try:
            v = float(m)
            if 0.0 <= v <= 1.0: return v
        except ValueError: pass
    t = text.lower()
    if "confidence: high" in t:    return 0.85
    if "confidence: medium" in t:  return 0.65
    if "confidence: low" in t:     return 0.35
    return 0.55


def _extract_bio(text: str) -> str:
    if not text: return ""
    if "BIOLOGICAL_INTERPRETATION:" in text:
        return text.split("BIOLOGICAL_INTERPRETATION:")[-1].strip()
    return text.strip()


def _classify_request(user_request: str) -> str:
    if not user_request: return "full_analysis"
    req = user_request.lower()
    vague = ["full", "complet", "tout", "analyse", "analyze", "rapport", "report",
             "evaluer", "évaluer", "check"]
    if any(k in req for k in vague) or len(req.split()) <= 3:
        return "full_analysis"
    if any(k in req for k in ["risque", "risk", "danger", "pathogène", "bacterie", "bactérie",
                                "contamina", "salmonella", "campylobacter"]):
        return "risk_only"
    if any(k in req for k in ["traitement", "treatment", "traiter", "désinfecter",
                                "disinfect", "purifier", "comment traiter"]):
        return "treatment_only"
    if any(k in req for k in ["valoris", "benefici", "bénéfici", "utiliser", "produit",
                                "compost", "engrais", "biostimulant", "vendre",
                                "normal", "peut-on", "peut on", "utilisable",
                                "quoi faire", "que faire", "saison"]):
        return "valorization_only"
    if any(k in req for k in ["roi", "revenu", "revenue", "profit", "tnd", "argent",
                                "économi", "economi", "investissement"]):
        return "roi_only"
    if any(k in req for k in ["environnement", "environment", "impact", "ecologie",
                                "écologie", "sol", "eau souterraine", "pollution",
                                "norme", "nt 106", "who", "conformit"]):
        return "environment_only"
    return "specific_question"


def _build_risk_query(water_desc: str, manure_desc: str) -> str:
    """Build dynamic risk search query from VLM results."""
    all_text = ((water_desc or "") + " " + (manure_desc or "")).lower()
    parts = []
    organism_map = [
        (r'egg.like.*present|oval.*structure.*present', "parasite eggs"),
        (r'hyphae.*present|spore.*present|fungal.*present', "fungal hyphae"),
        (r'bacteria.*dense|dense.*cluster', "dense bacterial clusters"),
        (r'urate.*abundant', "urate crystals high nitrogen"),
        (r'biofilm.*yes|biofilm.*present', "biofilm"),
        (r'salmonella', "Salmonella"),
        (r'campylobacter', "Campylobacter"),
    ]
    for pattern, label in organism_map:
        if re.search(pattern, all_text):
            parts.append(label)
    if not parts:
        parts = ["Salmonella Campylobacter poultry"]

    color_m = re.search(r'dominant[:\s]+(\w+)', all_text)
    if color_m and color_m.group(1) not in ("clear", "transparent"):
        parts.append(f"{color_m.group(1)} wastewater")

    turb_m = re.search(r'level[:\s]+(cloudy|opaque)', all_text)
    if turb_m:
        parts.append("turbid effluent")

    parts.append("poultry Tunisia WHO treatment protocol")
    return " ".join(parts)[:150]


def _build_valo_query(water_desc: str, manure_desc: str, waste_type: str) -> str:
    """Build dynamic valorization query from VLM results + waste type."""
    all_text = ((water_desc or "") + " " + (manure_desc or "")).lower()
    parts = []

    if waste_type == "water_only":
        parts.append("poultry wastewater liquid fertilizer fertigation")
        color_m = re.search(r'dominant[:\s]+(\w+)', all_text)
        if color_m:
            parts.append(f"{color_m.group(1)} effluent treatment")
        parts.append("Tunisia spring 2026 irrigation reuse")

    elif waste_type == "manure_only":
        if re.search(r'urate.*abundant|urate.*moderate', all_text):
            parts.append("poultry manure high nitrogen biostimulant urate crystals")
        else:
            parts.append("poultry manure compost fientes volaille")
        parts.append("Tunisia spring 2026 agronomique")

    else:
        parts.append("poultry manure wastewater compost biostimulant liquid fertilizer")
        parts.append("fientes volaille Tunisia spring 2026")

    return " ".join(parts)[:150]


def _determine_waste_type(
    water_rgb: str, water_micro: str,
    manure_rgb: str, manure_micro: str,
    water_result: dict, manure_result: dict,
) -> str:
    has_water_input  = _is_valid_path(water_rgb)  or _is_valid_path(water_micro)
    has_manure_input = _is_valid_path(manure_rgb) or _is_valid_path(manure_micro)
    has_water_result  = bool(water_result  and water_result.get("status") == "success")
    has_manure_result = bool(manure_result and manure_result.get("status") == "success")

    has_water  = has_water_input  or has_water_result
    has_manure = has_manure_input or has_manure_result

    if has_water and not has_manure:  return "water_only"
    if has_manure and not has_water:  return "manure_only"
    if has_water and has_manure:      return "both"
    return "unknown"


def _normalize_path(p) -> Optional[str]:
    if p is None:
        return None
    if not isinstance(p, str):
        return None
    p = p.strip()
    if not p or p.lower() in ("none", "null", ""):
        return None
    try:
        return str(Path(p).resolve()).replace("\\", "/")
    except Exception:
        return None


def _extract_description_from_result(result: dict, key: str) -> Optional[str]:
    if not result or result.get("status") != "success":
        return None
    val = result.get(key)
    if val and isinstance(val, str) and len(val.strip()) > 20:
        return val.strip()
    for alt_key in ["description", "analysis", "result", "content", "text"]:
        val = result.get(alt_key)
        if val and isinstance(val, str) and len(val.strip()) > 20:
            print(f"  [A2A] Used fallback key '{alt_key}' instead of '{key}'")
            return val.strip()
    for k, v in result.items():
        if isinstance(v, str) and len(v.strip()) > 100 and k not in ("session_id", "agent_id", "status", "error"):
            print(f"  [A2A] Used opportunistic key '{k}' for description")
            return v.strip()
    return None


# ── Main A2A pipeline ─────────────────────────────────────────

def run_agent_a2a(
    water_rgb:    str = None,
    water_micro:  str = None,
    manure_rgb:   str = None,
    manure_micro: str = None,
    user_request: str = None,
    status_callback=None,
) -> dict:

    print("\n" + "="*60)
    print("  WASTE INTELLIGENCE AGENT A2A v3 — Elmazraa")
    print("  Mode: A2A Multi-Agent (water:8001 + manure:8002)")
    print("  NEW: Environmental Impact Assessment enabled")
    print("="*60)

    water_rgb    = _normalize_path(water_rgb)
    water_micro  = _normalize_path(water_micro)
    manure_rgb   = _normalize_path(manure_rgb)
    manure_micro = _normalize_path(manure_micro)

    request_type = _classify_request(user_request)
    session_id   = str(uuid.uuid4())[:8]
    t_global_start = time.time()

    print(f"  Session: {session_id}")
    print(f"  water_rgb={water_rgb} water_micro={water_micro}")
    print(f"  manure_rgb={manure_rgb} manure_micro={manure_micro}")
    print(f"  Request: {user_request or 'full analysis'} [{request_type}]")

    def emit(event_type: str, agent: str = "", status: str = "", data: dict = None):
        event = {
            "type":      event_type,
            "agent":     agent,
            "status":    status,
            "data":      data or {},
            "timestamp": time.time(),
        }
        if status_callback:
            status_callback(event)

    emit("session_start", data={"session_id": session_id, "request_type": request_type})

    # ── Health check ───────────────────────────────────────────
    emit("discovery_start", status="running")
    health = asyncio.run(check_agents_health(AGENT_URLS))
    print(f"  [A2A] Health: {health}")
    emit("discovery_done", data={"health": health})

    # ── Load past memory ───────────────────────────────────────
    past_similar = None
    try:
        past_similar = memory_store.get_similar(user_request or "waste analysis", n=2)
        if past_similar:
            print(f"  [Memory] Found {len(past_similar)} past analyses")
            emit("memory_loaded", data={"count": len(past_similar)})
    except Exception as e:
        print(f"  [Memory] Error: {e}")

    # ── Discover agents ────────────────────────────────────────
    registry = AgentRegistry()
    asyncio.run(registry.discover(AGENT_URLS))
    available = registry.available_agents
    print(f"  [Registry] Available agents: {available}")
    emit("registry_ready", data={"available_agents": available})

    # ── Parallel delegation ────────────────────────────────────
    emit("delegation_start", status="running")

    def agent_status_cb(agent_name: str, status: str):
        emit("agent_status", agent=agent_name, status=status)

    delegation_result = asyncio.run(delegate_analyses_parallel(
        registry         = registry,
        water_rgb_path   = water_rgb,
        water_micro_path = water_micro,
        manure_rgb_path  = manure_rgb,
        manure_micro_path= manure_micro,
        user_request     = user_request,
        session_id       = session_id,
        status_callback  = agent_status_cb,
    ))

    water_result  = delegation_result.get("water_result")
    manure_result = delegation_result.get("manure_result")
    parallel_time = delegation_result.get("parallel_time_s", 0)
    agents_called = delegation_result.get("agents_called", [])
    delegation_errors = delegation_result.get("delegation_errors", [])

    emit("delegation_done", data={
        "agents_called":   agents_called,
        "parallel_time_s": parallel_time,
        "water_status":    water_result.get("status") if water_result else "not_called",
        "manure_status":   manure_result.get("status") if manure_result else "not_called",
        "errors":          delegation_errors,
    })

    print(f"  [A2A] Delegation complete in {parallel_time}s")
    if delegation_errors:
        print(f"  [A2A] Delegation errors: {delegation_errors}")

    # ── Extract VLM descriptions ──────────────────────────────
    water_description  = None
    manure_description = None
    water_conf         = 0.0
    manure_conf        = 0.0

    if water_result:
        print(f"  [A2A] water_result status={water_result.get('status')} "
              f"error={water_result.get('error', 'none')}")
        water_description = _extract_description_from_result(water_result, "water_description")
        if water_description:
            water_conf = water_result.get("confidence", 0.55) or _extract_confidence(water_description)
            print(f"  [A2A] Water analysis extracted: {len(water_description)} chars, conf={water_conf:.2f}")
        else:
            print(f"  [A2A] WARNING: Failed to extract water_description from result")

    if manure_result:
        print(f"  [A2A] manure_result status={manure_result.get('status')} "
              f"error={manure_result.get('error', 'none')}")
        manure_description = _extract_description_from_result(manure_result, "manure_description")
        if manure_description:
            manure_conf = manure_result.get("confidence", 0.55) or _extract_confidence(manure_description)
            print(f"  [A2A] Manure analysis extracted: {len(manure_description)} chars, conf={manure_conf:.2f}")
        else:
            print(f"  [A2A] WARNING: Failed to extract manure_description from result")

    if not water_description and not manure_description:
        error_details = []
        if water_result and water_result.get("error"):
            error_details.append(f"Water: {water_result['error']}")
        if manure_result and manure_result.get("error"):
            error_details.append(f"Manure: {manure_result['error']}")
        error_details.extend(delegation_errors)

        emit("error", data={
            "message": "All agents failed or returned empty descriptions",
            "errors": error_details,
        })
        return {
            "final_report": None,
            "error": f"Agent failures: {error_details}",
            "session_id": session_id,
            "agents_called": agents_called,
        }

    # ── Determine waste type ──────────────────────────────────
    waste_type = _determine_waste_type(
        water_rgb, water_micro, manure_rgb, manure_micro,
        water_result, manure_result,
    )
    print(f"  [A2A] Waste type detected: {waste_type}")
    emit("waste_type_detected", data={"waste_type": waste_type})

    # ── Post-aggregation pipeline ─────────────────────────────
    risk_assessment      = None
    valorization_plan    = None
    roi_result           = None
    environmental_impact = None   # ← NEW

    # search_risk
    if request_type not in ("roi_only", "environment_only"):
        emit("tool_start", agent="master", data={"tool": "search_risk"})
        risk_query = _build_risk_query(water_description or "", manure_description or "")
        print(f"\n  [Pipeline] search_risk query: '{risk_query}'")
        try:
            risk_assessment = search_risk.invoke({"query": risk_query})
            emit("tool_done", agent="master", data={
                "tool": "search_risk",
                "result_len": len(risk_assessment or ""),
            })
        except Exception as e:
            print(f"  [Pipeline] search_risk error: {e}")
            emit("tool_error", agent="master", data={"tool": "search_risk", "error": str(e)})

    # search_valorization
    if request_type not in ("risk_only", "treatment_only", "environment_only"):
        emit("tool_start", agent="master", data={"tool": "search_valorization"})
        valo_query = _build_valo_query(water_description or "", manure_description or "", waste_type)
        print(f"\n  [Pipeline] search_valorization query: '{valo_query}'")
        try:
            valorization_plan = search_valorization.invoke({"query": valo_query})
            emit("tool_done", agent="master", data={
                "tool": "search_valorization",
                "result_len": len(valorization_plan or ""),
            })
        except Exception as e:
            print(f"  [Pipeline] search_valorization error: {e}")
            emit("tool_error", agent="master", data={"tool": "search_valorization", "error": str(e)})

    # calculate_roi
    if valorization_plan:
        emit("tool_start", agent="master", data={"tool": "calculate_roi"})
        print(f"\n  [Pipeline] calculate_roi...")
        try:
            roi_result = calculate_roi.invoke({
                "water_liters":      0.0,
                "manure_kg":         0.0,
                "product_type":      "",
                "price_per_unit":    0.0,
                "context":           "",
                "valorization_plan": valorization_plan,
            })
            emit("tool_done", agent="master", data={
                "tool": "calculate_roi",
                "result_len": len(roi_result or ""),
            })
        except Exception as e:
            print(f"  [Pipeline] calculate_roi error: {e}")
            emit("tool_error", agent="master", data={"tool": "calculate_roi", "error": str(e)})

    # ── NEW: assess_environmental_impact ──────────────────────
    # Always run environmental assessment when we have visual data
    emit("tool_start", agent="master", data={"tool": "assess_environmental_impact"})
    print(f"\n  [Pipeline] assess_environmental_impact...")
    try:
        environmental_impact = assess_environmental_impact.invoke({
            "water_description":  water_description  or "",
            "manure_description": manure_description or "",
            "waste_type":         waste_type,
            "risk_assessment":    risk_assessment    or "",
        })
        emit("tool_done", agent="master", data={
            "tool": "assess_environmental_impact",
            "result_len": len(environmental_impact or ""),
        })
        print(f"  [Pipeline] Environmental assessment: {len(environmental_impact or '')} chars")
    except Exception as e:
        print(f"  [Pipeline] assess_environmental_impact error: {e}")
        emit("tool_error", agent="master", data={"tool": "assess_environmental_impact", "error": str(e)})
        environmental_impact = None
    # ── END NEW ───────────────────────────────────────────────

    # save_to_memory
    emit("tool_start", agent="master", data={"tool": "save_to_memory"})
    memory_saved = False
    try:
        water_bio  = _extract_bio(water_description or "")[:200]
        manure_bio = _extract_bio(manure_description or "")[:200]
        description = " | ".join(filter(None, [water_bio[:100], manure_bio[:100]]))
        if not description:
            description = f"A2A session {session_id} — waste_type={waste_type}"

        risk_level = "unknown"
        if risk_assessment:
            m = re.search(r'RISK_LEVEL:\s*(\w+)', risk_assessment, re.IGNORECASE)
            if m: risk_level = m.group(1)

        product = "unknown"
        if valorization_plan:
            m = re.search(r'RECOMMENDED_PRODUCT:\s*(.+)', valorization_plan)
            if m: product = m.group(1).strip()[:50]

        mem_result = save_to_memory.invoke({
            "description":    description,
            "risk":           risk_level,
            "product":        product,
            "report_summary": f"A2A session {session_id} | waste={waste_type} | conf_water={water_conf:.2f} conf_manure={manure_conf:.2f}",
        })
        memory_saved = True
        print(f"  [Pipeline] Memory: {mem_result}")
        emit("tool_done", agent="master", data={"tool": "save_to_memory", "result": mem_result})
    except Exception as e:
        print(f"  [Pipeline] save_to_memory error: {e}")
        emit("tool_error", agent="master", data={"tool": "save_to_memory", "error": str(e)})

    # generate_report — with environmental_impact injected
    emit("tool_start", agent="master", data={"tool": "generate_report"})
    print(f"\n  [Pipeline] generate_report...")
    print(f"    water={len(water_description or '')} manure={len(manure_description or '')}")
    print(f"    risk={len(risk_assessment or '')} valo={len(valorization_plan or '')} roi={len(roi_result or '')}")
    print(f"    env_impact={len(environmental_impact or '')}  ← NEW")

    try:
        final_report = generate_report.invoke({
            "water_analysis":          water_description   or "",
            "manure_analysis":         manure_description  or "",
            "risk_assessment":         risk_assessment     or "",
            "valorization_plan":       valorization_plan   or "",
            "roi_result":              roi_result          or "",
            "environmental_impact":    environmental_impact or "",   # ← NEW param
            "context":                 str(past_similar)[:300] if past_similar else "",
            "user_request":            user_request        or "",
        })
        emit("tool_done", agent="master", data={
            "tool": "generate_report",
            "result_len": len(final_report or ""),
        })
    except Exception as e:
        print(f"  [Pipeline] generate_report error: {e}")
        emit("tool_error", agent="master", data={"tool": "generate_report", "error": str(e)})
        final_report = f"Report generation failed: {e}"

    total_time = round(time.time() - t_global_start, 2)
    print(f"\n{'='*60}")
    print(f"  A2A PIPELINE COMPLETE — {total_time}s total ({parallel_time}s parallel)")
    print(f"{'='*60}")

    emit("session_done", data={
        "session_id":    session_id,
        "total_time_s":  total_time,
        "parallel_time_s": parallel_time,
        "waste_type":    waste_type,
        "agents_called": agents_called,
        "memory_saved":  memory_saved,
    })

    result = {
        "session_id":             session_id,
        "final_report":           final_report,
        "water_description":      water_description,
        "manure_description":     manure_description,
        "risk_assessment":        risk_assessment,
        "valorization_plan":      valorization_plan,
        "roi_result":             roi_result,
        "environmental_impact":   environmental_impact,   # ← NEW
        "memory_saved":           memory_saved,
        "waste_type":             waste_type,
        "agents_called":          agents_called,
        "parallel_time_s":        parallel_time,
        "total_time_s":           total_time,
        "water_confidence":       water_conf,
        "manure_confidence":      manure_conf,
    }

    # Print report to console
    report_text = final_report or ""
    if "ELMAZRAA WASTE INTELLIGENCE REPORT" in report_text:
        after = report_text.split("ELMAZRAA WASTE INTELLIGENCE REPORT", 1)[-1]
        lines = after.split("\n")
        content = []
        skip = True
        for line in lines:
            if skip:
                s = line.strip()
                if s and not s.startswith("=") and not s.startswith("Generated") \
                        and not s.startswith("Scope") and not s.startswith("Request"):
                    skip = False
                    content.append(line)
            else:
                content.append(line)
        display = "\n".join(content)
    else:
        display = report_text

    print(f"\n{'='*60}")
    print(display[:3000])
    if len(display) > 3000:
        print(f"\n  ... [full report in outputs/reports/]")
    print("="*60)

    return result