

import sys, os, uuid, re, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
from pathlib import Path
load_dotenv()

from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from langchain_groq import ChatGroq

from state import AgentState
from memory.long_term import LongTermMemory
from tools.tool_water        import analyze_water
from tools.tool_manure       import analyze_manure
from tools.tool_adaptive     import adaptive_intelligence
from tools.tool_risk         import search_risk
from tools.tool_valorization import search_valorization
from tools.tool_roi          import calculate_roi
from tools.tool_memory       import save_to_memory
from tools.tool_report       import generate_report
from tools.tool_environmental import search_environmental_impact


memory_store = LongTermMemory()

ALL_TOOLS = [
    analyze_water, analyze_manure, adaptive_intelligence,
    search_risk, search_environmental_impact, search_valorization, calculate_roi,
    save_to_memory, generate_report,
]

REUSABLE_TOOLS  = {"analyze_water", "analyze_manure", "search_risk", "search_valorization"}
ONCE_ONLY_TOOLS = {"adaptive_intelligence", "save_to_memory", "generate_report"}


# ── Utilitaires ───────────────────────────────────────────────

def _extract_confidence(text: str) -> float:
    if not text:
        return 0.0
    for m in re.findall(r'(?:global|confidence)[:\s]+([0-9.]+)', text, re.IGNORECASE):
        try:
            v = float(m)
            if 0.0 <= v <= 1.0:
                return v
        except ValueError:
            continue
    t = text.lower()
    if "confidence: high" in t:    return 0.85
    if "confidence: medium" in t:  return 0.65
    if "confidence: low" in t:     return 0.35
    if "no_images" in t:           return 0.0
    if "analysis_complete" in t:   return 0.70
    strong_patterns = [
        r'urate.*abundant|abundant.*urate',
        r'egg.like.*present|present.*egg.like',
        r'bacteria.*dense|dense.*cluster',
        r'parasite.*present|present.*parasite',
        r'hyphae.*present|spore.*present',
        r'oval.*structure.*present',
        r'double.*wall.*structur',
        r'cell_count_estimate.*abundant',
        r'particle_density.*very_high|particle_density.*high',
        r'biofilm_presence.*yes',
        r'fat_globules.*many',
        r'urate_crystals.*moderate|urate_crystals.*abundant',
    ]
    strong_count = sum(1 for p in strong_patterns if re.search(p, t))
    if strong_count >= 3: return 0.82
    if strong_count >= 2: return 0.74
    if strong_count >= 1: return 0.66
    visual_patterns = [
        r'dominant.*(?:yellow|brown|grey|black|orange)',
        r'turbidity.*(?:cloudy|opaque)',
        r'sediment_visible.*yes',
        r'shapes_observed.*(?:rod|cocci|filament|mixed)',
        r'clustering.*(?:colonies|dense)',
    ]
    visual_count = sum(1 for p in visual_patterns if re.search(p, t))
    if visual_count >= 3: return 0.62
    if visual_count >= 2: return 0.58
    if visual_count >= 1: return 0.54
    return 0.50


def _extract_bio(text: str) -> str:
    if not text:
        return ""
    if "BIOLOGICAL_INTERPRETATION:" in text:
        return text.split("BIOLOGICAL_INTERPRETATION:")[-1].strip()
    return text.strip()


def _extract_sources_block(text: str) -> str:
    if not text:
        return ""
    if "\nSOURCES:\n" in text:
        block = text.split("\nSOURCES:\n")[-1].strip()
        return block[:1200]
    if "REFERENCES_USED:" in text:
        block = text.split("REFERENCES_USED:")[-1].strip()
        return block[:1200]
    refs = re.findall(r'REF_\d+:[^\n]+', text)
    if refs:
        return "\n".join(refs[:8])
    return ""


# ── v17 NEW: Extract real VLM indicators for dynamic query building ──

def _extract_vlm_query_indicators(state: AgentState) -> dict:
    """
    v17 BUG #3: Extraire les vrais indicateurs visuels des analyses VLM
    pour construire des queries search_risk et search_valorization précises.
    Retourne un dict avec les indicateurs clés trouvés.
    """
    water_text  = state.get("water_description") or ""
    manure_text = state.get("manure_description") or ""
    all_text    = (water_text + " " + manure_text).lower()

    indicators = {
        "organisms":    [],
        "water_color":  "",
        "water_turb":   "",
        "manure_color": "",
        "manure_texture": "",
        "risk_keywords": [],
        "nitrogen_level": "",
        "has_parasites": False,
        "has_fungi": False,
        "has_bacteria": False,
        "has_urate": False,
    }

    # Micro-organismes et structures
    organism_map = [
        (r'salmonella',                   "Salmonella"),
        (r'campylobacter',                "Campylobacter"),
        (r'e\.?\s*coli|escherichia',      "E.coli"),
        (r'clostridium',                  "Clostridium"),
        (r'aspergillus',                  "Aspergillus"),
        (r'fusarium',                     "Fusarium"),
        (r'coccidia|eimeria',             "Eimeria coccidia"),
        (r'ascaris',                      "Ascaris"),
        (r'cryptosporidium',              "Cryptosporidium"),
        (r'egg.like.*present|egg-like.*present|oval.*structure.*present', "parasite eggs"),
        (r'hyphae.*present|spore.*present|fungi.*present',                "fungal hyphae spores"),
        (r'bacteria.*dense|dense.*cluster|bacterial.*cluster.*dense',     "dense bacterial clusters"),
        (r'biofilm.*yes|biofilm.*present',                                 "biofilm"),
    ]
    for pattern, label in organism_map:
        if re.search(pattern, all_text):
            indicators["organisms"].append(label)
            if "parasite" in label or "egg" in label:
                indicators["has_parasites"] = True
            if "fungal" in label or "fungi" in label:
                indicators["has_fungi"] = True
            if "bacteria" in label or "biofilm" in label:
                indicators["has_bacteria"] = True

    # Cristaux d'urate (indicateur azote)
    urate_match = re.search(r'urate[_\s]crystals[:\s]*(\w+)', all_text)
    if urate_match:
        level = urate_match.group(1)
        if level in ("moderate", "abundant"):
            indicators["has_urate"] = True
            indicators["nitrogen_level"] = f"urate_crystals_{level}"
            indicators["organisms"].append(f"urate crystals {level}")

    # Couleur et turbidité eau
    color_m = re.search(r'dominant[:\s-]+(\w+)', water_text.lower())
    if color_m:
        indicators["water_color"] = color_m.group(1)
    turb_m = re.search(r'turbidity[:\s-]+.*?level[:\s-]+(\w+)', water_text.lower())
    if not turb_m:
        turb_m = re.search(r'level[:\s-]+(\w+)', water_text.lower())
    if turb_m:
        indicators["water_turb"] = turb_m.group(1)

    # Texture fientes
    tex_m = re.search(r'type[:\s-]+(\w+)', manure_text.lower())
    if tex_m:
        indicators["manure_texture"] = tex_m.group(1)

    return indicators


def _build_risk_query_from_vlm(state: AgentState) -> str:
    """
    v17 BUG #3: Construire une query search_risk précise basée sur
    les vrais indicateurs VLM au lieu d'une query générique.
    """
    ind = _extract_vlm_query_indicators(state)
    parts = []

    if ind["organisms"]:
        # Prendre les 3 premiers organismes les plus spécifiques
        parts.extend(ind["organisms"][:3])
    else:
        # Fallback sur indicateurs génériques de volaille
        parts.append("Salmonella Campylobacter")

    if ind["has_parasites"]:
        parts.append("parasite eggs treatment")
    if ind["has_fungi"]:
        parts.append("fungal contamination")
    if ind["water_color"] and ind["water_color"] not in ("clear", "transparent"):
        parts.append(f"{ind['water_color']} wastewater")
    if ind["water_turb"] in ("cloudy", "opaque"):
        parts.append("turbid wastewater")

    parts.append("poultry Tunisia WHO treatment")

    query = " ".join(parts)[:150]
    return query


def _build_valo_query_from_vlm(state: AgentState, waste_type: str) -> str:
    """
    v17 BUG #3: Construire une query search_valorization précise basée sur
    les vrais indicateurs VLM ET le type de déchet.
    """
    ind = _extract_vlm_query_indicators(state)
    parts = []

    if waste_type == "water_only":
        parts.append("poultry wastewater liquid organic fertilizer fertigation")
        if ind["water_color"]:
            parts.append(f"{ind['water_color']} effluent")
        parts.append("Tunisia spring 2026 irrigation reuse")

    elif waste_type == "manure_only":
        if ind["has_urate"]:
            parts.append("poultry manure high nitrogen biostimulant")
            parts.append(ind["nitrogen_level"].replace("_", " "))
        else:
            parts.append("poultry manure compost")
        if ind["manure_texture"]:
            parts.append(ind["manure_texture"])
        parts.append("fientes volaille Tunisia spring 2026")

    else:  # both
        if ind["has_urate"]:
            parts.append("poultry manure urate crystals nitrogen biostimulant")
        else:
            parts.append("poultry manure compost biostimulant")
        parts.append("wastewater liquid fertilizer Tunisia spring 2026")

    query = " ".join(parts)[:150]
    return query


def _compress_context(state: AgentState, max_per_section: int = 400) -> str:
    parts = []

    if state.get("water_description"):
        conf = _extract_confidence(state["water_description"])
        water_text = state["water_description"]
        critical = _extract_critical_indicators_flat(water_text)
        section = f"WATER_ANALYSIS_CONFIDENCE={conf:.2f}\n"
        if critical:
            section += f"WATER_CRITICAL_INDICATORS: {critical}\n"
        section += f"WATER_FULL_ANALYSIS:\n{water_text}"
        parts.append(section)

    if state.get("manure_description"):
        conf = _extract_confidence(state["manure_description"])
        manure_text = state["manure_description"]
        critical = _extract_critical_indicators_flat(manure_text)
        section = f"MANURE_ANALYSIS_CONFIDENCE={conf:.2f}\n"
        if critical:
            section += f"MANURE_CRITICAL_INDICATORS: {critical}\n"
        section += f"MANURE_FULL_ANALYSIS:\n{manure_text}"
        parts.append(section)

    if state.get("adaptive_result"):
        parts.append(f"ADAPTIVE_ASSESSMENT:\n{state['adaptive_result'][:200]}")

    if state.get("risk_assessment"):
        risk_text = state["risk_assessment"]
        risk_sources = _extract_sources_block(risk_text)
        risk_main = risk_text[:800]
        section = f"RISK_ASSESSMENT:\n{risk_main}"
        if risk_sources:
            section += f"\nRISK_SOURCES_FOR_REPORT:\n{risk_sources}"
        parts.append(section)

    if state.get("valorization_plan"):
        valo_text = state["valorization_plan"]
        valo_sources = _extract_sources_block(valo_text)
        valo_main = valo_text[:800]
        section = f"VALORIZATION_PLAN:\n{valo_main}"
        if valo_sources:
            section += f"\nVALO_SOURCES_FOR_REPORT:\n{valo_sources}"
        parts.append(section)

    if state.get("roi_result"):
        parts.append(f"ROI_CALCULATION:\n{state['roi_result'][:600]}")

    if state.get("context_collected"):
        parts.append(f"ENGINEER_CONTEXT:\n{state['context_collected'][:300]}")

    if state.get("session_summary"):
        parts.append(f"SESSION_MEMORY:\n{state['session_summary'][:300]}")

    has_water  = bool(state.get("water_description"))
    has_manure = bool(state.get("manure_description"))
    has_water_rgb   = bool(state.get("water_image_rgb"))
    has_water_micro = bool(state.get("water_image_micro"))
    has_manure_rgb  = bool(state.get("manure_image_rgb"))
    has_manure_micro= bool(state.get("manure_image_micro"))

    sample_types = []
    if has_water:  sample_types.append("WATER")
    if has_manure: sample_types.append("MANURE")
    parts.append(
        f"SAMPLES_ANALYZED: {', '.join(sample_types) if sample_types else 'NONE'}\n"
        f"WATER_IMAGES: rgb={'YES' if has_water_rgb else 'NO'} micro={'YES' if has_water_micro else 'NO'}\n"
        f"MANURE_IMAGES: rgb={'YES' if has_manure_rgb else 'NO'} micro={'YES' if has_manure_micro else 'NO'}"
    )

    return "\n\n".join(parts) if parts else "No analyses performed yet."


def _extract_critical_indicators_flat(text: str) -> str:
    if not text:
        return ""
    indicators = []
    t = text.lower()
    patterns_labels = [
        (r'urate[_\s]crystals[:\s]*([^\n,]+)', "cristaux_urate"),
        (r'egg.like[_\s]structures[:\s]*([^\n,]+)', "structures_ovales"),
        (r'bacterial[_\s]clusters[:\s]*([^\n,]+)', "clusters_bacteriens"),
        (r'hyphae[_\s]filaments[:\s]*([^\n,]+)', "hyphes_fongiques"),
        (r'spores[:\s]*([^\n,]+)', "spores"),
        (r'parasite[_\s]like[_\s]structures[:\s]*([^\n,]+)', "parasites"),
        (r'parasite[:\s]*([^\n,]+)', "parasites"),
        (r'cell_count_estimate[:\s]*([^\n,]+)', "densite_cellulaire"),
        (r'turbidity[_\s]level[:\s]*([^\n,]+)', "turbidite"),
    ]
    for pattern, label in patterns_labels:
        m = re.search(pattern, t)
        if m:
            val = m.group(1).strip()[:40].replace("\n", " ")
            if val and val not in ("not_visible", "absent", "none", "no"):
                indicators.append(f"{label}:{val}")
    return " | ".join(indicators) if indicators else ""


def _build_state_summary(state: AgentState) -> str:
    lines = []
    w_rgb   = state.get("water_image_rgb") or ""
    w_micro = state.get("water_image_micro") or ""
    if w_rgb or w_micro:
        paths = []
        if w_rgb:   paths.append(f"rgb='{w_rgb}'")
        if w_micro: paths.append(f"micro='{w_micro}'")
        lines.append(f"WATER_IMAGES_AVAILABLE: {', '.join(paths)}")
    else:
        lines.append("WATER_IMAGES_AVAILABLE: NONE — do NOT call analyze_water")

    m_rgb   = state.get("manure_image_rgb") or ""
    m_micro = state.get("manure_image_micro") or ""
    if m_rgb or m_micro:
        paths = []
        if m_rgb:   paths.append(f"rgb='{m_rgb}'")
        if m_micro: paths.append(f"micro='{m_micro}'")
        lines.append(f"MANURE_IMAGES_AVAILABLE: {', '.join(paths)}")
    else:
        lines.append("MANURE_IMAGES_AVAILABLE: NONE — do NOT call analyze_manure")

    lines.append(f"\nUSER_REQUEST: {state.get('user_request') or 'full analysis'}")

    completed = []
    if state.get("water_description"): completed.append("analyze_water")
    if state.get("manure_description"):completed.append("analyze_manure")
    if state.get("adaptive_result"):   completed.append("adaptive_intelligence")
    if state.get("risk_assessment"):   completed.append("search_risk")
    if state.get("valorization_plan"): completed.append("search_valorization")
    if state.get("roi_result"):        completed.append("calculate_roi")
    if state.get("memory_saved"):      completed.append("save_to_memory")
    if state.get("final_report"):      completed.append("generate_report")

    lines.append(f"COMPLETED_TOOLS: {', '.join(completed) if completed else 'none yet'}")

    once_done = [t for t in ONCE_ONLY_TOOLS if t in completed]
    if once_done:
        lines.append(f"DO_NOT_CALL_AGAIN: {', '.join(once_done)}")

    data_available = []
    if state.get("water_description"):  data_available.append("water_analysis_data=YES")
    if state.get("manure_description"): data_available.append("manure_analysis_data=YES")
    if state.get("risk_assessment"):    data_available.append("risk_data=YES")
    if state.get("valorization_plan"):  data_available.append("valorization_data=YES")
    if state.get("roi_result"):         data_available.append("roi_data=YES")
    if data_available:
        lines.append(f"DATA_READY_FOR_REPORT: {', '.join(data_available)}")

    # v17: statut explicite de chaque outil obligatoire
    lines.append(f"RISK_STATUS:  {'DONE' if state.get('risk_assessment') else 'MISSING — search_risk MUST be called'}")
    lines.append(f"ENV_STATUS: {'DONE' if state.get('environmental_impact') else 'MISSING — search_environmental_impact MUST be called after search_risk'}")
    lines.append(f"VALO_STATUS:  {'DONE' if state.get('valorization_plan') else 'MISSING — search_valorization MUST be called'}")
    lines.append(f"ROI_STATUS:   {'DONE' if state.get('roi_result') else ('MISSING — calculate_roi MUST be called' if state.get('valorization_plan') else 'WAITING for valorization_plan')}")
    lines.append(f"MEMORY_STATUS: {'DONE' if state.get('memory_saved') else 'MISSING — save_to_memory MUST be called before generate_report'}")

    has_water_only  = bool(state.get("water_description")) and not bool(state.get("manure_description"))
    has_manure_only = bool(state.get("manure_description")) and not bool(state.get("water_description"))
    has_both        = bool(state.get("water_description")) and bool(state.get("manure_description"))
    if has_water_only:
        lines.append("WASTE_TYPE: WATER_ONLY — valorization = liquid fertilizer/irrigation, NOT compost")
    elif has_manure_only:
        lines.append("WASTE_TYPE: MANURE_ONLY — valorization = compost/biostimulant")
    elif has_both:
        lines.append("WASTE_TYPE: BOTH — water→liquid fertilizer, manure→compost/biostimulant")

    return "\n".join(lines)


def _has_enough_for_report(state: AgentState) -> bool:
    return bool(
        state.get("water_description") or
        state.get("manure_description") or
        state.get("risk_assessment")
    )


def _is_valorization_question(user_request: str) -> bool:
    if not user_request:
        return False
    keywords = [
        "beneficier", "bénéficier", "valoris", "comment", "utiliser",
        "saison", "profit", "revenue", "vendre", "vente", "invest",
        "how to use", "how can", "benefit", "season", "utilisation",
        "comment on peut", "comment se", "se beneficier", "quoi faire",
        "que faire"
    ]
    req_lower = user_request.lower()
    return any(k in req_lower for k in keywords)


def _classify_request(user_request: str) -> str:
    if not user_request:
        return "full_analysis"
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
    return "specific_question"


def _recent_tool_names(messages: list, n: int = 6) -> list:
    return [
        tc['name']
        for msg in messages[-n:]
        if hasattr(msg, 'tool_calls') and msg.tool_calls
        for tc in msg.tool_calls
    ]


def _build_session_summary(state: AgentState) -> str:
    parts = []
    if state.get("water_description"):
        conf = _extract_confidence(state["water_description"])
        critical = _extract_critical_indicators_flat(state["water_description"])
        water_text = state["water_description"].lower()
        indicators = []
        for kw in ["turbidity", "turbide", "cloudy", "foam", "mousse", "biofilm",
                   "crystal", "urate", "bacteria", "parasit", "fungi", "egg-like", "oval"]:
            if kw in water_text:
                indicators.append(kw)
        ind_str = (critical or ", ".join(indicators[:3])) if (critical or indicators) else "analysée"
        parts.append(f"Eau analysée (conf={conf:.2f}, indicators: {ind_str[:80]})")

    if state.get("manure_description"):
        conf = _extract_confidence(state["manure_description"])
        critical = _extract_critical_indicators_flat(state["manure_description"])
        manure_text = state["manure_description"].lower()
        indicators = []
        for kw in ["urate", "crystal", "parasite", "fungi", "feather", "plume",
                   "nitrogen", "azote", "spore", "hyphae", "egg-like", "oval"]:
            if kw in manure_text:
                indicators.append(kw)
        ind_str = (critical or ", ".join(indicators[:3])) if (critical or indicators) else "analysées"
        parts.append(f"Fientes analysées (conf={conf:.2f}, indicators: {ind_str[:80]})")

    if state.get("risk_assessment"):
        risk_text = state["risk_assessment"]
        level_match = re.search(r'RISK_LEVEL:\s*(\w+)', risk_text, re.IGNORECASE)
        if level_match:
            parts.append(f"Risque: {level_match.group(1)}")

    if state.get("valorization_plan"):
        prod_match = re.search(r'RECOMMENDED_PRODUCT:\s*(.+)', state["valorization_plan"])
        if prod_match:
            parts.append(f"Produit recommandé: {prod_match.group(1).strip()[:50]}")

    if state.get("roi_result"):
        roi_match = re.search(r'Net monthly[:\s]+([0-9,. ]+TND)', state["roi_result"], re.IGNORECASE)
        if roi_match:
            parts.append(f"ROI mensuel estimé: {roi_match.group(1).strip()}")
        else:
            parts.append("ROI calculé")

    return " | ".join(parts) if parts else ""


# ── v17 NEW: Determine what MUST be called next (deterministic) ──

def _get_mandatory_next_tool(state: AgentState, request_type: str) -> str | None:
    """
    v17 BUG #1 + #2: Retourne le prochain outil OBLIGATOIRE à appeler,
    ou None si aucun outil n'est obligatoire à ce stade.

    Logique déterministe — pas de LLM libre quand un outil est obligatoire.
    Couvre TOUS les request_types, y compris full_analysis.
    """
    has_water_analysis  = bool(state.get("water_description"))
    has_manure_analysis = bool(state.get("manure_description"))
    has_any_analysis    = has_water_analysis or has_manure_analysis
    has_risk   = bool(state.get("risk_assessment"))
    has_valo   = bool(state.get("valorization_plan"))
    has_roi    = bool(state.get("roi_result"))
    has_memory = bool(state.get("memory_saved"))
    has_report = bool(state.get("final_report"))

    if has_report:
        return None

    # Après analyse visuelle : search_risk est OBLIGATOIRE avant tout le reste
    # (sauf si c'est un flow roi_only qui n'a pas besoin de risk)
    if has_any_analysis and not has_risk and request_type != "roi_only":
        return "search_risk"
    

    # Après risk (ou pour roi_only) : search_valorization OBLIGATOIRE
    # (sauf pour risk_only / treatment_only qui n'ont pas besoin de valo)
    if has_any_analysis and not has_valo and request_type not in ("risk_only", "treatment_only"):
        if has_risk or request_type == "roi_only":
            return "search_valorization"
    has_env = bool(state.get("environmental_impact"))
    if has_any_analysis and has_risk and not has_env and request_type not in ("roi_only",):
      return "search_environmental_impact"

    # Après valorization : calculate_roi OBLIGATOIRE
    if has_valo and not has_roi:
        return "calculate_roi"

    # Avant generate_report : save_to_memory OBLIGATOIRE
    if has_any_analysis and not has_memory and (has_risk or has_valo):
        return "save_to_memory"

    return None  # Aucun outil obligatoire — LLM peut choisir librement


# ── Direct report / save calls ────────────────────────────────

def _call_generate_report_directly(state: AgentState) -> str:
    def _ensure_str(val) -> str:
        if val is None: return ""
        if isinstance(val, dict): return "\n".join(f"{k}: {v}" for k, v in val.items())
        if isinstance(val, list): return "\n".join(str(item) for item in val)
        return str(val).strip()

    water_data   = _ensure_str(state.get("water_description"))
    manure_data  = _ensure_str(state.get("manure_description"))
    risk_data    = _ensure_str(state.get("risk_assessment"))
    valo_data    = _ensure_str(state.get("valorization_plan"))
    roi_data     = _ensure_str(state.get("roi_result"))
    context_data = _ensure_str(state.get("context_collected"))
    user_req     = _ensure_str(state.get("user_request"))

    print(f"  [Report Direct v17] water={len(water_data)} manure={len(manure_data)}")
    print(f"  [Report Direct v17] risk={len(risk_data)} valo={len(valo_data)} roi={len(roi_data)}")

    if not water_data and not manure_data:
        print("  [Report Direct v17] WARNING: Both empty — checking messages...")
        messages = state.get("messages", [])
        for msg in messages:
            if isinstance(msg, ToolMessage):
                name = str(getattr(msg, 'name', '') or '')
                content = str(msg.content or '').strip()
                if name == "analyze_water" and len(content) > 20 and not water_data:
                    water_data = content
                elif name == "analyze_manure" and len(content) > 20 and not manure_data:
                    manure_data = content

    try:
        result = generate_report.invoke({
            "water_analysis":    water_data,
            "manure_analysis":   manure_data,
            "risk_assessment":   risk_data,
            "valorization_plan": valo_data,
            "roi_result":        roi_data,
            "context":           context_data,
            "user_request":      user_req,
        })
        return result
    except Exception as e:
        return f"Emergency report failed: {e}"


def _call_save_to_memory_directly(state: AgentState) -> bool:
    try:
        water_bio  = _extract_bio(state.get("water_description") or "")[:300]
        manure_bio = _extract_bio(state.get("manure_description") or "")[:300]
        risk_txt   = state.get("risk_assessment") or ""
        valo_txt   = state.get("valorization_plan") or ""
        description = " | ".join(filter(None, [water_bio[:150], manure_bio[:150]]))
        if not description:
            description = "Waste analysis session — no visual description available"
        risk_level = "unknown"
        m = re.search(r'RISK_LEVEL:\s*(\w+)', risk_txt, re.IGNORECASE)
        if m: risk_level = m.group(1)
        product = "unknown"
        m = re.search(r'RECOMMENDED_PRODUCT:\s*(.+)', valo_txt)
        if m: product = m.group(1).strip()[:50]
        report_summary = state.get("session_summary") or "Session analysis completed"
        result = save_to_memory.invoke({
            "description": description,
            "risk": risk_level,
            "product": product,
            "report_summary": report_summary,
        })
        print(f"  [Direct Save] {result}")
        return True
    except Exception as e:
        print(f"  [Direct Save] Failed: {e}")
        return False


# ── System Prompt ─────────────────────────────────────────────

def _build_system_prompt(request_type: str, waste_type: str = "both") -> str:
    if waste_type == "water_only":
        valo_rule = (
            "WASTE TYPE = WATER ONLY:\n"
            "  → Valorization = liquid organic fertilizer / fertigation / irrigation reuse\n"
            "  → NEVER recommend 'compost' for wastewater — compost is for SOLID manure only\n"
            "  → Products: treated effluent for irrigation, liquid biostimulant, biogas if organic load high\n"
        )
    elif waste_type == "manure_only":
        valo_rule = (
            "WASTE TYPE = MANURE ONLY:\n"
            "  → Valorization = compost, biostimulant, pellets, biogas\n"
            "  → Urate crystals abundant → high nitrogen → biostimulant or liquid fertilizer\n"
        )
    else:
        valo_rule = (
            "WASTE TYPE = BOTH:\n"
            "  → Water → liquid fertilizer/fertigation\n"
            "  → Manure → compost/biostimulant\n"
            "  → Recommend SEPARATELY for each waste stream\n"
        )

    base = f"""You are an intelligent waste analysis agent for Elmazraa poultry plant (Tunisia).
Analyze poultry wastewater and/or manure samples, assess risks, recommend valorization.
Base ALL conclusions ONLY on what tools return. Never invent data.

{valo_rule}

TOOLS AND WHEN TO USE THEM:
- analyze_water   → ONLY if WATER_IMAGES_AVAILABLE shows paths (not NONE)
- analyze_manure  → ONLY if MANURE_IMAGES_AVAILABLE shows paths (not NONE)
- adaptive_intelligence → ONCE, after visual analyses, to evaluate quality
- search_risk     → MANDATORY after visual analysis — query MUST use real VLM indicators
- search_valorization → MANDATORY after search_risk — query MUST reflect waste type + real indicators
- calculate_roi   → MANDATORY when valorization_plan is available and roi not yet done
- save_to_memory  → MANDATORY ONCE, ALWAYS before generate_report
- generate_report → ONCE, final step — ONLY after risk + valo + roi + memory are ALL done

CRITICAL RULES:
- generate_report MUST NOT be called unless: risk_assessment=DONE, valorization_plan=DONE,
  roi_result=DONE, memory_saved=DONE — ALL four must be DONE
- If RISK_STATUS=MISSING → call search_risk IMMEDIATELY
- If VALO_STATUS=MISSING → call search_valorization IMMEDIATELY  
- If ROI_STATUS=MISSING → call calculate_roi IMMEDIATELY
- If MEMORY_STATUS=MISSING → call save_to_memory IMMEDIATELY
- adaptive_intelligence, save_to_memory, generate_report: ONE TIME ONLY
- CRITICAL v17: When calling generate_report, copy the COMPLETE text from
  WATER_FULL_ANALYSIS and MANURE_FULL_ANALYSIS sections as water_analysis/manure_analysis
- ALL generate_report parameters MUST be plain strings (never objects/dicts)

XAI REASONING FORMAT:
Before choosing your next tool, you MUST provide your internal reasoning in this format:
REASONING: [why this step is next, what you observed]
CHOSEN_TOOL: [tool name]
CONFIDENCE: [0.0-1.0]
"""

    flows = {
        "full_analysis": """
MANDATORY FLOW FOR FULL ANALYSIS (ALL steps required):
1. analyze_water (if images available)
2. analyze_manure (if images available)
3. adaptive_intelligence (once — evaluate quality)
4. search_risk ← MANDATORY — use REAL VLM indicators in query (organisms, colors, turbidity seen)
5. search_valorization ← MANDATORY — use REAL characteristics + waste type in query
6. calculate_roi ← MANDATORY — call as soon as valorization_plan exists
7. save_to_memory ← MANDATORY — NEVER skip
8. generate_report ← ONLY when steps 4+5+6+7 are ALL DONE
""",
        "risk_only": """
MANDATORY FLOW FOR RISK/TREATMENT QUESTION:
1. analyze_water (if images available)
2. analyze_manure (if images available)
3. search_risk ← MANDATORY — query = real organisms/indicators seen in VLM
4. save_to_memory ← MANDATORY — after search_risk
5. generate_report (focus on risk + treatment)
""",
        "treatment_only": """
MANDATORY FLOW FOR TREATMENT QUESTION:
1. analyze_water (if images available)
2. analyze_manure (if images available)
3. search_risk ← MANDATORY — query = specific pathogens/conditions found
4. save_to_memory ← MANDATORY
5. generate_report (focus on treatment protocols)
""",
        "valorization_only": """
MANDATORY FLOW FOR VALORIZATION QUESTION:
1. analyze_water (if images available)
2. analyze_manure (if images available)
3. search_risk ← MANDATORY even for valo questions (safety check)
4. search_valorization ← MANDATORY — query MUST match WASTE TYPE + real indicators
5. calculate_roi ← MANDATORY
6. save_to_memory ← MANDATORY
7. generate_report (focus on valorization + economics)
""",
        "roi_only": """
MANDATORY FLOW FOR ROI/ECONOMIC QUESTION:
1. analyze_water (if images available)
2. analyze_manure (if images available)
3. search_valorization ← MANDATORY — query MUST match WASTE TYPE
4. calculate_roi ← MANDATORY
5. save_to_memory ← MANDATORY
6. generate_report (focus on economics)
""",
        "specific_question": """
MANDATORY FLOW FOR SPECIFIC QUESTION:
1. analyze_water (if images available)
2. analyze_manure (if images available)
3. search_risk ← MANDATORY
4. search_valorization ← MANDATORY — query MUST match WASTE TYPE
5. calculate_roi ← MANDATORY if valorization_plan available
6. save_to_memory ← MANDATORY
7. generate_report (answer the specific question directly)
"""
    }

    return base + flows.get(request_type, flows["full_analysis"])


# ── Nodes ──────────────────────────────────────────────────────

def agent_node(state: AgentState) -> dict:
    count = state.get("iteration_count", 0)

    if state.get("_direct_report_injected"):
        return {"messages": state.get("messages", []), "iteration_count": count}

    if state.get("final_report") or count >= 15:
        return {"messages": state.get("messages", []), "iteration_count": count}

    if count > 0:
        time.sleep(1)

    all_messages = state.get("messages", [])
    user_request = state.get("user_request") or ""
    is_valo_question = _is_valorization_question(user_request)
    request_type = _classify_request(user_request)

    has_water  = bool(state.get("water_description") or state.get("water_image_rgb") or state.get("water_image_micro"))
    has_manure = bool(state.get("manure_description") or state.get("manure_image_rgb") or state.get("manure_image_micro"))
    if has_water and not has_manure:
        waste_type = "water_only"
    elif has_manure and not has_water:
        waste_type = "manure_only"
    else:
        waste_type = "both"

    session_summary = _build_session_summary(state)

    # ── v17: Determine mandatory next tool FIRST ───────────────
    mandatory_next = _get_mandatory_next_tool(state, request_type)

    # ── Force logic (v16 preserved + v17 extended) ─────────────
    force_report           = False
    force_save_then_report = False
    force_roi              = False
    recent = _recent_tool_names(all_messages, n=8)

    if (state.get("valorization_plan") and
            not state.get("roi_result") and
            not state.get("final_report") and
            "calculate_roi" not in recent):
        force_roi = True
        print("  [Force ROI] Valorization done but no ROI → forcing calculate_roi")

    if not force_roi and not mandatory_next and count >= 4 and _has_enough_for_report(state) and not state.get("final_report"):
        adaptive_count = recent.count("adaptive_intelligence")

        if not state.get("memory_saved"):
            tools_done = sum([
                bool(state.get("water_description")),
                bool(state.get("manure_description")),
                bool(state.get("risk_assessment")),
                bool(state.get("valorization_plan")),
            ])
            if tools_done >= 2:
                force_save_then_report = True
                print("  [Force] Enough data → forcing save_to_memory then generate_report")

        elif adaptive_count >= 3:
            force_report = True
            print("  [Force] Adaptive loop x3 → forcing generate_report")

        elif adaptive_count >= 2:
            if is_valo_question:
                if state.get("valorization_plan") and state.get("roi_result"):
                    if state.get("memory_saved"):
                        force_report = True
                    else:
                        force_save_then_report = True
            else:
                if state.get("memory_saved"):
                    force_report = True
                else:
                    force_save_then_report = True

    if force_save_then_report and state.get("memory_saved"):
        force_save_then_report = False
        force_report = True

    if force_report and not state.get("memory_saved"):
        force_report = False
        force_save_then_report = True

    if force_report and state.get("valorization_plan") and not state.get("roi_result"):
        force_report = False
        force_roi = True
        print("  [Force ROI override] generate_report blocked — ROI missing")

    # v17: force_report bloqué si risk ou valo manquent encore
    if force_report and not state.get("risk_assessment"):
        force_report = False
        mandatory_next = "search_risk"
        print("  [v17 Block] force_report blocked — risk_assessment missing")

    if force_report and not state.get("valorization_plan") and request_type not in ("risk_only", "treatment_only"):
        force_report = False
        mandatory_next = "search_valorization"
        print("  [v17 Block] force_report blocked — valorization_plan missing")

    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=os.getenv("GROQ_API_KEY"),
        temperature=0.0,
        max_tokens=2000,
    ).bind_tools(ALL_TOOLS)

    state_summary   = _build_state_summary(state)
    current_context = _compress_context(state, max_per_section=400)

    if len(all_messages) > 5:
        first_human = next((m for m in all_messages if isinstance(m, HumanMessage)), None)
        recent_msgs = all_messages[-4:]
        history = ([first_human] + recent_msgs) \
                  if (first_human and first_human not in recent_msgs) else recent_msgs
    else:
        history = all_messages

    # ── v17: Build next_instruction with mandatory tool hint ────
    if force_report:
        next_instruction = (
            "All data is ready AND memory is saved. "
            "Call generate_report NOW.\n"
            "CRITICAL v17: Copy the COMPLETE text from WATER_FULL_ANALYSIS as water_analysis parameter.\n"
            "Copy the COMPLETE text from MANURE_FULL_ANALYSIS as manure_analysis parameter.\n"
            "Copy RISK_ASSESSMENT section as risk_assessment.\n"
            "Copy VALORIZATION_PLAN section as valorization_plan.\n"
            "Copy ROI_CALCULATION section as roi_result.\n"
            "ALL parameters = plain strings, NEVER objects or dicts.\n"
            "DO NOT truncate the water_analysis or manure_analysis — pass them COMPLETE."
        )
    elif force_save_then_report:
        next_instruction = (
            "You have enough data to finalize. "
            "FIRST call save_to_memory with the analysis summary as plain strings. "
            "THEN call generate_report — copy COMPLETE data from WATER_FULL_ANALYSIS and "
            "MANURE_FULL_ANALYSIS sections as water_analysis/manure_analysis parameters.\n"
            "DO NOT truncate — pass them COMPLETE."
        )
    elif force_roi:
        next_instruction = (
            "Valorization plan is ready. "
            "Call calculate_roi NOW. "
            "Copy the valorization_plan text from VALORIZATION_PLAN section. "
            "Use water_liters=0 and manure_kg=0 if quantities unknown."
        )

    # v17 BUG #1 + #3: Instructions déterministes pour outils obligatoires
    elif mandatory_next == "search_risk":
        risk_query = _build_risk_query_from_vlm(state)
        next_instruction = (
            f"⚠️ MANDATORY NEXT TOOL: search_risk\n"
            f"RISK_STATUS is MISSING — you MUST call search_risk before anything else.\n"
            f"Use this EXACT query (built from real VLM findings): \"{risk_query}\"\n"
            f"The query is based on what was actually observed in the images.\n"
            f"DO NOT call generate_report, save_to_memory, or calculate_roi before search_risk."
        )
    elif mandatory_next == "search_valorization":
        valo_query = _build_valo_query_from_vlm(state, waste_type)
        next_instruction = (
            f"⚠️ MANDATORY NEXT TOOL: search_valorization\n"
            f"VALO_STATUS is MISSING — you MUST call search_valorization now.\n"
            f"Use this EXACT query (built from real VLM findings + waste type): \"{valo_query}\"\n"
            f"WASTE TYPE is {waste_type.upper()} — query already reflects this.\n"
            f"DO NOT call generate_report or save_to_memory before search_valorization."
        )
    elif mandatory_next == "calculate_roi":
        next_instruction = (
            f"⚠️ MANDATORY NEXT TOOL: calculate_roi\n"
            f"ROI_STATUS is MISSING — you MUST call calculate_roi now.\n"
            f"Use valorization_plan from VALORIZATION_PLAN section.\n"
            f"Use water_liters=0 and manure_kg=0 if quantities unknown."
        )
    elif mandatory_next == "save_to_memory":
        next_instruction = (
            f"⚠️ MANDATORY NEXT TOOL: save_to_memory\n"
            f"MEMORY_STATUS is MISSING — you MUST call save_to_memory before generate_report.\n"
            f"Use actual findings as description (plain string)."
        )
    else:
        # LLM libre — aucun outil obligatoire manquant
        next_instruction = (
            f"REQUEST TYPE: {request_type.upper()} | WASTE TYPE: {waste_type.upper()}\n"
            "All mandatory tools are accounted for. "
            "What is the best next action?\n"
            "CRITICAL v17: When calling generate_report, copy COMPLETE WATER_FULL_ANALYSIS "
            "and MANURE_FULL_ANALYSIS as water_analysis/manure_analysis — DO NOT truncate."
        )

    messages = [
        SystemMessage(content=_build_system_prompt(request_type, waste_type)),
        *history,
        HumanMessage(content=(
            f"CURRENT STATE:\n{state_summary}\n\n"
            f"ANALYSES SO FAR (USE THESE AS PARAMETERS FOR generate_report):\n"
            f"[NOTE: WATER_FULL_ANALYSIS and MANURE_FULL_ANALYSIS are COMPLETE — pass them as-is]\n"
            f"{current_context}\n\n"
            f"SESSION SUMMARY: {session_summary or 'Session just started'}\n\n"
            f"{next_instruction}"
        ))
    ]

    response = llm.invoke(messages)

    print(f"\n[Agent] Iteration {count + 1} [{request_type}] [{waste_type}]")
    if hasattr(response, 'tool_calls') and response.tool_calls:
        called = response.tool_calls[0]['name']
        print(f"  → Calling: {called}")

        # v17: warn si LLM ignore le mandatory_next
        if mandatory_next and called != mandatory_next:
            print(f"  ⚠ [v17 WARNING] LLM chose '{called}' but mandatory was '{mandatory_next}'")
            # On ne bloque pas ici — should_stop bloquera generate_report si nécessaire

        if called == 'generate_report':
            args = response.tool_calls[0].get('args', {})
            for param in ['water_analysis', 'manure_analysis', 'risk_assessment',
                          'valorization_plan', 'roi_result']:
                val = args.get(param, '')
                val_type = type(val).__name__
                val_len = len(str(val)) if val else 0
                print(f"  → {param}: type={val_type} len={val_len}")
                if isinstance(val, (dict, list)):
                    print(f"  ⚠ WARNING: {param} is {val_type} — will be intercepted!")
    else:
        print(f"  → Direct response")

    # v14: Extract CoT reasoning
    tool_called = "direct_response"
    if hasattr(response, 'tool_calls') and response.tool_calls:
        tool_called = response.tool_calls[0]['name']
    
    cot_entry = _extract_xai_cot(response.content, count + 1, tool_called)
    current_cot = state.get("xai_cot_trace") or []

    return {
        "messages": [response],
        "iteration_count": count + 1,
        "session_summary": session_summary,
        "xai_cot_trace": current_cot + [cot_entry]
    }


def _extract_xai_cot(response_content: str, count: int, tool_called: str) -> dict:
    """v14: Extract CoT from LLM response."""
    reasoning = ""
    conf = 0.5
    if response_content:
        r_match = re.search(r'REASONING:?\s*(.*?)(?:\nCHOSEN_TOOL:|$)', response_content, re.DOTALL | re.IGNORECASE)
        if r_match: reasoning = r_match.group(1).strip()
        c_match = re.search(r'CONFIDENCE:?\s*([0-9.]+)', response_content, re.IGNORECASE)
        if c_match: 
            try: conf = float(c_match.group(1))
            except: pass
            
    return {
        "iter": count,
        "reasoning": reasoning or "Executing standard analysis pipeline step.",
        "tool": tool_called,
        "confidence": conf,
        "timestamp": time.strftime("%H:%M:%S")
    }


def update_state_node(state: AgentState) -> AgentState:
    messages = state.get("messages", [])
    updates  = {}

    new_tool_msgs = []
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            new_tool_msgs.append(msg)
        elif hasattr(msg, 'tool_calls') and msg.tool_calls:
            break

    seen = set()
    for msg in new_tool_msgs:
        content = str(msg.content or "").strip()
        name    = str(getattr(msg, 'name', '') or '').strip()

        if not content or not name or name in seen:
            continue
        seen.add(name)

        print(f"  [Update] {name}")

        if name == "analyze_water":
            cur = state.get("water_description", "")
            if not cur or (len(content) > 20 and content[:100] != (cur or "")[:100]):
                updates["water_description"] = content

        elif name == "analyze_manure":
            cur = state.get("manure_description", "")
            if not cur or (len(content) > 20 and content[:100] != (cur or "")[:100]):
                updates["manure_description"] = content

        elif name == "adaptive_intelligence":
            updates["adaptive_result"] = content
            if "ACTION: ASK_QUESTION" in content:
                m = re.search(r'QUESTION:\s*(.+?)(?:\nCONFIDENCE|\nACTION|$)', content, re.DOTALL)
                if m:
                    question = m.group(1).strip()[:250]
                    already  = state.get("questions_asked") or []
                    if question not in already:
                        print(f"\n  [Question] {question}")
                        try:
                            answer = input("  Your answer: ").strip()
                        except (EOFError, KeyboardInterrupt):
                            answer = "No answer provided"
                        ctx = state.get("context_collected") or ""
                        updates["context_collected"] = ctx + f"\nQ: {question}\nA: {answer}"
                        updates["questions_asked"] = already + [question]

            elif "ACTION: REQUEST_IMAGE" in content:
                m = re.search(r'INSTRUCTION:\s*(.+?)(?:\nCONFIDENCE|\nACTION|$)', content, re.DOTALL)
                if m:
                    instruction = m.group(1).strip()[:200]
                    print(f"\n  [Image Requested] {instruction}")
                    try:
                        new_path = input("  New image path (Enter to skip): ").strip().strip('"').strip("'")
                    except (EOFError, KeyboardInterrupt):
                        new_path = ""
                    if new_path:
                        if "manure" in instruction.lower() or "fient" in instruction.lower():
                            updates["manure_image_rgb"] = new_path
                        else:
                            updates["water_image_rgb"] = new_path

        elif name == "search_risk":
            cur = state.get("risk_assessment", "")
            if not cur or len(content) > len(cur):
                updates["risk_assessment"] = content

        elif name == "search_valorization":
            cur = state.get("valorization_plan", "")
            if not cur or len(content) > len(cur):
                updates["valorization_plan"] = content

        elif name == "calculate_roi":
            updates["roi_result"] = content
            print(f"  [Update ROI] Stored roi_result len={len(content)}")

        elif name == "save_to_memory":
            updates["memory_saved"] = True

        elif name == "generate_report":
            updates["final_report"] = content

    merged = {**state, **updates}
    updates["session_summary"] = _build_session_summary(merged)

    if "final_report" in updates:
        report_content = updates["final_report"] or ""
        has_water_data  = bool(merged.get("water_description"))
        has_manure_data = bool(merged.get("manure_description"))
        report_says_no_analysis = (
            "non analysé" in report_content.lower() or
            "not analyzed" in report_content.lower() or
            "pas été analysé" in report_content.lower() or
            "non fourni cette session" in report_content.lower()
        )
        if (has_water_data or has_manure_data) and report_says_no_analysis:
            print("  [v17 WARNING] Report says 'non analysé' but data exists → triggering direct report")
            direct_report = _call_generate_report_directly(merged)
            if direct_report and len(direct_report) > 500:
                updates["final_report"] = direct_report
                print("  [v17] Direct report generated successfully")

    # v14: Dynamic Indicator Attribution
    if any(k in updates for k in ["water_description", "manure_description", "risk_assessment", "valorization_plan", "roi_result"]):
        updates["xai_attribution"] = _extract_xai_attribution({**state, **updates})

    return {**state, **updates}


def _extract_xai_attribution(state: AgentState) -> dict:
    """
    v14: Extract visual indicators and trace their influence.
    """
    water_text = state.get("water_description") or ""
    manure_text = state.get("manure_description") or ""
    all_text = (water_text + " " + manure_text).lower()
    
    attribution = state.get("xai_attribution") or {
        "indicators": [],
        "risk_drivers": [],
        "valo_drivers": [],
        "roi_drivers": []
    }
    
    indicator_patterns = [
        ("urate_crystals", r"urate[_\s]crystals", 0.8),
        ("pathogen_eggs", r"egg[_\s]like|parasite[_\s]egg|oval[_\s]structure", 0.9),
        ("bacterial_clusters", r"bacterial[_\s]cluster|bacteria[_\s]dense", 0.7),
        ("fungal_hyphae", r"hyphae|spore|fungi", 0.85),
        ("turbidity_high", r"turbidity.*(?:opaque|cloudy|level[_\s]high)", 0.6),
        ("color_anomaly", r"dominant.*(?:yellow|brown|black|grey|orange)", 0.5)
    ]
    
    new_indicators = []
    for name, pattern, base_weight in indicator_patterns:
        if re.search(pattern, all_text):
            weight = base_weight
            if "abundant" in all_text: weight += 0.1
            if "moderate" in all_text: weight += 0.05
            if "rare" in all_text: weight -= 0.1
            
            new_indicators.append({
                "name": name.replace("_", " ").title(),
                "score": round(min(weight, 1.0), 2),
                "evidence": f"Found in visual analysis"
            })
            
    if new_indicators:
        attribution["indicators"] = new_indicators
    
    # Trace causal links
    if state.get("risk_assessment") and not attribution["risk_drivers"]:
        if any("Pathogen" in i["name"] for i in new_indicators):
            attribution["risk_drivers"].append({"indicator": "Pathogen Eggs", "impact": "High risk of parasitosis detected in sample"})
        if any("Bacterial" in i["name"] for i in new_indicators):
            attribution["risk_drivers"].append({"indicator": "Bacterial Clusters", "impact": "Bacterial load indicates potential contamination"})

    if state.get("valorization_plan") and not attribution["valo_drivers"]:
        if any("Urate" in i["name"] for i in new_indicators):
            attribution["valo_drivers"].append({"indicator": "Urate Crystals", "impact": "Significant nitrogen markers found"})
        if any("Turbidity" in i["name"] for i in new_indicators):
            attribution["valo_drivers"].append({"indicator": "High Turbidity", "impact": "Organic matter concentration supports fertigation use"})
            
    if state.get("roi_result") and not attribution["roi_drivers"]:
        attribution["roi_drivers"].append({"indicator": "Economic Model", "impact": "Calculation based on recommended valorization path"})

    return attribution


def _sanitize_tool_args(tool_call: dict, state: AgentState) -> dict:
    args = dict(tool_call.get('args', {}))
    tool_name = tool_call.get('name', '')

    if tool_name == 'generate_report':
        def _to_str(val) -> str:
            if val is None: return ""
            if isinstance(val, dict): return "\n".join(f"{k}: {v}" for k, v in val.items())
            if isinstance(val, list): return "\n".join(str(i) for i in val)
            return str(val).strip()

        for param in ['water_analysis', 'manure_analysis', 'risk_assessment',
                      'valorization_plan', 'roi_result', 'context', 'user_request']:
            if param in args:
                args[param] = _to_str(args[param])

        param_map = {
            'water_analysis':    ('water_description', 500),
            'manure_analysis':   ('manure_description', 500),
            'risk_assessment':   ('risk_assessment', 100),
            'valorization_plan': ('valorization_plan', 100),
            'roi_result':        ('roi_result', 20),
            'context':           ('context_collected', 20),
            'user_request':      ('user_request', 5),
        }
        for param, (state_key, min_len) in param_map.items():
            current = args.get(param, "")
            current_len = len(str(current).strip()) if current else 0
            state_val = state.get(state_key) or ""
            state_val_str = _to_str(state_val)
            state_val_len = len(state_val_str.strip())

            if state_val_len > current_len and state_val_len > min_len:
                args[param] = state_val_str
                print(f"  [Sanitize v17] {param}: {current_len} → {state_val_len} chars (from state)")
            elif current_len < min_len and state_val_len > 0:
                args[param] = state_val_str
                print(f"  [Sanitize v17] {param}: too short ({current_len}) → using state ({state_val_len} chars)")

    return args


def should_stop(state: AgentState) -> str:
    if state.get("_direct_report_injected"):
        print("  [Stop] Direct report was injected → stopping.")
        return END

    if state.get("final_report"):
        print("  [Stop] Final report done.")
        return END

    count = state.get("iteration_count", 0)
    if count >= 15:
        print("  [Stop] Max iterations — generating emergency report...")
        if not state.get("memory_saved"):
            _call_save_to_memory_directly(state)
        report = _call_generate_report_directly(state)
        print(f"\n{report[:800]}")
        return END

    messages = state.get("messages", [])
    if not messages:
        return "agent"

    last = messages[-1]

    if not hasattr(last, 'tool_calls') or not last.tool_calls:
        if _has_enough_for_report(state) and not state.get("final_report"):
            print("  [Auto-report] Direct response with data → routing to agent for report")
            return "agent"
        print("  [Stop] Direct response.")
        return END

    tool_name = last.tool_calls[0]['name']

    if tool_name in ONCE_ONLY_TOOLS:
        already = {
            "adaptive_intelligence": bool(state.get("adaptive_result")),
            "save_to_memory":        bool(state.get("memory_saved")),
            "generate_report":       bool(state.get("final_report")),
        }
        if already.get(tool_name, False):
            print(f"  [Skip] {tool_name} already done → re-routing")
            return "agent"

    if tool_name == "generate_report" and not state.get("memory_saved"):
        print("  [Hold] generate_report requested but save_to_memory not done → re-routing")
        return "agent"

    if tool_name == "generate_report" and state.get("valorization_plan") and not state.get("roi_result"):
        print("  [Hold] generate_report requested but ROI missing → forcing calculate_roi")
        return "agent"

    # ── v17 BUG #1: Bloquer generate_report si risk ou valo manquent ──
    request_type = _classify_request(state.get("user_request") or "")

    if tool_name == "generate_report" and not state.get("risk_assessment") and request_type not in ("roi_only",):
        print("  [v17 Block] generate_report blocked — risk_assessment MISSING → re-routing to search_risk")
        return "agent"

    if (tool_name == "generate_report" and
            not state.get("valorization_plan") and
            request_type not in ("risk_only", "treatment_only")):
        print("  [v17 Block] generate_report blocked — valorization_plan MISSING → re-routing to search_valorization")
        return "agent"

    # ── v17: Intercepter generate_report avec params invalides (v16 preserved) ──
    if tool_name == "generate_report":
        args = last.tool_calls[0].get('args', {})
        state_has_data = (
            bool(state.get("water_description")) or
            bool(state.get("manure_description")) or
            bool(state.get("risk_assessment"))
        )

        has_object_params = any(
            isinstance(args.get(p), (dict, list))
            for p in ['water_analysis', 'manure_analysis', 'risk_assessment',
                      'valorization_plan', 'roi_result']
        )

        water_param  = str(args.get('water_analysis', '') or '')
        manure_param = str(args.get('manure_analysis', '') or '')
        risk_param   = str(args.get('risk_assessment', '') or '')
        water_state  = str(state.get("water_description") or '')
        manure_state = str(state.get("manure_description") or '')

        water_truncated  = bool(water_state)  and len(water_param.strip())  < min(500, len(water_state) // 2)
        manure_truncated = bool(manure_state) and len(manure_param.strip()) < min(500, len(manure_state) // 2)
        params_empty = (
            len(water_param.strip())  < 20 and
            len(manure_param.strip()) < 20 and
            len(risk_param.strip())   < 20
        )

        if has_object_params:
            print("  [v17 INTERCEPT] generate_report has object/dict params → direct call")
            if not state.get("memory_saved"):
                _call_save_to_memory_directly(state)
            direct_report = _call_generate_report_directly(state)
            state["final_report"] = direct_report
            state["memory_saved"] = True
            state["_direct_report_injected"] = True
            return END

        if state_has_data and params_empty:
            print("  [v17 INTERCEPT] generate_report called with empty params but state has data")
            if not state.get("memory_saved"):
                _call_save_to_memory_directly(state)
            direct_report = _call_generate_report_directly(state)
            state["final_report"] = direct_report
            state["memory_saved"] = True
            state["_direct_report_injected"] = True
            return END

        if water_truncated or manure_truncated:
            print(f"  [v17 INTERCEPT] VLM data truncated → direct call with full data")
            if not state.get("memory_saved"):
                _call_save_to_memory_directly(state)
            direct_report = _call_generate_report_directly(state)
            state["final_report"] = direct_report
            state["memory_saved"] = True
            state["_direct_report_injected"] = True
            return END

    if len(messages) >= 4:
        recent = _recent_tool_names(messages, n=4)
        if (len(recent) >= 2
                and recent[-1] == recent[-2]
                and tool_name not in REUSABLE_TOOLS):
            print(f"  [Loop] {tool_name} repeated → re-routing")
            return "agent"

    return "tools"


# ── Custom ToolNode avec sanitization v17 ─────────────────────

class SanitizedToolNode(ToolNode):
    def __init__(self, tools, state_ref: dict = None):
        super().__init__(tools)
        self._state_ref = state_ref or {}

    def invoke(self, input, config=None, **kwargs):
        messages = input.get("messages", []) if isinstance(input, dict) else []
        state = input if isinstance(input, dict) else {}

        if messages:
            last = messages[-1]
            if hasattr(last, 'tool_calls') and last.tool_calls:
                needs_sanitize = any(tc.get('name') == 'generate_report' for tc in last.tool_calls)

                if needs_sanitize:
                    import copy
                    new_last = copy.deepcopy(last)
                    sanitized_any = False
                    for i, tc in enumerate(new_last.tool_calls):
                        if tc.get('name') == 'generate_report':
                            sanitized_args = _sanitize_tool_args(tc, state)
                            if sanitized_args != tc.get('args', {}):
                                new_last.tool_calls[i] = {**tc, 'args': sanitized_args}
                                sanitized_any = True
                                print(f"  [SanitizedToolNode v17] generate_report args sanitized")

                    if sanitized_any:
                        new_messages = list(messages[:-1]) + [new_last]
                        if isinstance(input, dict):
                            input = {**input, 'messages': new_messages}

        return super().invoke(input, config, **kwargs)


# ── Graph ─────────────────────────────────────────────────────

def build_agent():
    graph = StateGraph(AgentState)
    graph.add_node("agent",  agent_node)
    graph.add_node("tools",  SanitizedToolNode(ALL_TOOLS))
    graph.add_node("update", update_state_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges(
        "agent", should_stop,
        {"tools": "tools", "agent": "agent", END: END}
    )
    graph.add_edge("tools",  "update")
    graph.add_edge("update", "agent")
    return graph.compile(checkpointer=MemorySaver())


# ── Entry Point ───────────────────────────────────────────────

def _normalize_path(p):
    if p:
        return str(Path(p).resolve()).replace("\\", "/")
    return p


def run_agent(
    water_rgb:    str = None,
    water_micro:  str = None,
    manure_rgb:   str = None,
    manure_micro: str = None,
    user_request: str = None,
) -> AgentState:

    print("\n" + "="*60)
    print("  WASTE INTELLIGENCE AGENT v17 — Elmazraa")
    print("  Model: LLaMA 3.3 70B (Groq) — Full Agentic")
    print("="*60)

    water_rgb    = _normalize_path(water_rgb)
    water_micro  = _normalize_path(water_micro)
    manure_rgb   = _normalize_path(manure_rgb)
    manure_micro = _normalize_path(manure_micro)

    request_type = _classify_request(user_request)

    print(f"  Inputs:")
    print(f"    water_rgb:    {water_rgb or 'None'}")
    print(f"    water_micro:  {water_micro or 'None'}")
    print(f"    manure_rgb:   {manure_rgb or 'None'}")
    print(f"    manure_micro: {manure_micro or 'None'}")
    print(f"    request:      {user_request or 'full analysis'}")
    print(f"    request_type: {request_type}")

    past_similar = None
    past_summary = ""
    try:
        past_similar = memory_store.get_similar(user_request or "waste analysis", n=2)
        if past_similar:
            past_str = str(past_similar)[:300]
            past_summary = f"PAST_SIMILAR_ANALYSES: {past_str}"
            print(f"  [Memory] Found {len(past_similar)} past similar analyses")
    except Exception as e:
        print(f"  [Memory] No past analyses found: {e}")

    initial_state = AgentState(
        water_image_rgb=water_rgb,
        water_image_micro=water_micro,
        manure_image_rgb=manure_rgb,
        manure_image_micro=manure_micro,
        user_request=user_request,
        water_description=None,
        manure_description=None,
        adaptive_result=None,
        sampling_done=False,
        sampling_confidence=None,
        images_requested=[],
        context_collected=past_summary if past_summary else None,
        questions_asked=[],
        risk_assessment=None,
        valorization_plan=None,
        roi_result=None,
        past_similar=past_similar,
        memory_saved=False,
        final_report=None,
        report_path=None,
        next_tool=None,
        next_args={},
        iteration_count=0,
        agent_scratchpad=None,
        error=None,
        messages=[],
        session_summary="",
        _direct_report_injected=False,
        xai_cot_trace=[],
        xai_attribution={}
    )

    agent  = build_agent()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    result = agent.invoke(initial_state, config=config)

    print("\n" + "="*60)
    print("  AGENT FINISHED")
    print("="*60)

    report = result.get("final_report", "")
    if report:
        if "ELMAZRAA WASTE INTELLIGENCE REPORT" in report:
            after_header = report.split("ELMAZRAA WASTE INTELLIGENCE REPORT", 1)[-1]
            lines = after_header.split("\n")
            content_lines = []
            skip_header = True
            for line in lines:
                if skip_header:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("=") \
                            and not stripped.startswith("Generated") \
                            and not stripped.startswith("Scope") \
                            and not stripped.startswith("Path") \
                            and not stripped.startswith("Request"):
                        skip_header = False
                        content_lines.append(line)
                else:
                    content_lines.append(line)
            display = "\n".join(content_lines)
        else:
            display = report

        print(f"\n  Report saved to: outputs/reports/")
        print(f"{'='*60}")
        print(display[:3000])
        if len(display) > 3000:
            print(f"\n  ... [rapport complet dans outputs/reports/]")
        print("="*60)
    else:
        for msg in reversed(result.get("messages", [])):
            if hasattr(msg, 'content') and isinstance(msg.content, str) and msg.content.strip():
                print(f"\nAgent Response:\n{msg.content}")
                break

    print("="*60)
    return result
