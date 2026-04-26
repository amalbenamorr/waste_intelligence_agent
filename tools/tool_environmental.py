# ============================================================
# tools/tool_environmental.py — Environmental Impact Tool
# Recherche dynamique Tavily + LLM interpreter
# Normes réelles : NT 106, WHO, FAO, ISO 14001, OMS
# ============================================================

import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.tools import tool
from utils.models import call_llm

try:
    from tavily import TavilyClient
    _TAVILY_AVAILABLE = True
except ImportError:
    _TAVILY_AVAILABLE = False

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")


def _tavily_search(query: str, max_results: int = 5) -> list[dict]:
    """Execute a Tavily search and return results list."""
    if not _TAVILY_AVAILABLE or not TAVILY_API_KEY:
        return []
    try:
        client = TavilyClient(api_key=TAVILY_API_KEY)
        results = client.search(query=query, max_results=max_results, search_depth="advanced")
        return results.get("results", [])
    except Exception as e:
        print(f"  [Env Tool] Tavily error: {e}")
        return []


def _format_search_results(results: list[dict]) -> str:
    """Format Tavily results for LLM consumption."""
    if not results:
        return "No external sources found."
    formatted = []
    for i, r in enumerate(results, 1):
        title   = r.get("title", "Unknown")
        url     = r.get("url", "")
        content = r.get("content", "")[:600]
        formatted.append(f"SOURCE_{i}: {title}\nURL: {url}\nEXCERPT: {content}")
    return "\n\n".join(formatted)


def _build_env_query(water_desc: str, manure_desc: str, waste_type: str) -> str:
    """
    Build a precise environmental impact query based on real VLM indicators.
    """
    all_text = ((water_desc or "") + " " + (manure_desc or "")).lower()
    parts = []

    # Pathogens detected
    pathogen_map = [
        (r'salmonella',              "Salmonella contamination soil groundwater"),
        (r'campylobacter',           "Campylobacter environmental persistence"),
        (r'e\.?\s*coli|escherichia', "E.coli groundwater contamination"),
        (r'egg.like|oval.*struct',   "parasite eggs soil contamination"),
        (r'hyphae|spores|fungal',    "fungal spores air soil contamination"),
        (r'biofilm.*yes|biofilm.*present', "biofilm water ecosystem impact"),
        (r'urate.*abundant',         "urate crystals nitrogen soil leaching"),
    ]
    for pattern, label in pathogen_map:
        if re.search(pattern, all_text):
            parts.append(label)

    # Waste type specific
    if waste_type == "water_only":
        color_m = re.search(r'dominant[:\s]+(\w+)', (water_desc or "").lower())
        color = color_m.group(1) if color_m else "brown"
        parts.append(f"poultry wastewater {color} effluent environmental discharge")
        parts.append("Tunisia NT 106.002 wastewater treatment standards")
    elif waste_type == "manure_only":
        parts.append("poultry manure fientes nitrogen phosphorus leaching")
        parts.append("Tunisia agricultural land application standards 2025")
    else:
        parts.append("poultry waste water manure environmental impact Tunisia")
        parts.append("WHO 2006 guidelines safe reuse wastewater agriculture")

    # Always include key standards
    parts.append("FAO environmental guidelines organic waste poultry")
    parts.append("ISO 14001 environmental management poultry Tunisia 2024")

    query = " ".join(parts)[:180]
    return query


INTERPRETER_PROMPT = """You are an environmental compliance expert for poultry waste management in Tunisia.

You receive:
1. Visual analyses of wastewater/manure samples (biological indicators)
2. Web search results from authoritative environmental standards sources

Your task: produce a structured environmental impact assessment based ONLY on real data provided.

MANDATORY FORMAT — return EXACTLY this structure:

ENVIRONMENTAL_RISK_LEVEL:
- level: (low / medium / high / critical)
- justification: (one sentence based on observed indicators)

SOIL_IMPACT:
- nitrogen_leaching_risk: (low / medium / high)
- phosphorus_runoff_risk: (low / medium / high)
- pathogen_soil_contamination: (low / medium / high)
- evidence: (what visual indicators support this)

WATER_IMPACT:
- groundwater_contamination_risk: (low / medium / high)
- surface_water_risk: (low / medium / high)
- aquifer_threat: (yes / no / uncertain)
- evidence: (visual + source based)

AIR_IMPACT:
- ammonia_emission_risk: (low / medium / high)
- bioaerosol_risk: (low / medium / high)
- odor_nuisance: (low / medium / high)
- evidence: (based on observed density/composition)

BIODIVERSITY_IMPACT:
- flora_risk: (low / medium / high)
- fauna_risk: (low / medium / high)
- ecosystem_disruption: (low / medium / high)

COMPLIANCE_STATUS:
- NT_106_002_Tunisia: (compliant / non_compliant / needs_treatment / not_assessed)
- WHO_2006_guidelines: (compliant / non_compliant / needs_treatment / not_assessed)
- key_limits_exceeded: (list exceeded parameters or "none identified")
- certification_possible: (yes_after_treatment / yes_now / no)

MITIGATION_MEASURES:
- immediate: (concrete action within 24h)
- short_term: (within 1 month)
- long_term: (within 6 months)
- estimated_cost_tnd: (rough estimate or "insufficient data")

ENVIRONMENTAL_OPPORTUNITIES:
- carbon_credits_potential: (yes / no / investigate)
- green_certification_eligible: (yes / no / conditional)
- circular_economy_score: (0-10)
- notes: (brief justification)

CONFIDENCE_ENV:
- global: (0.0 to 1.0)
- limiting_factor: (what reduced confidence, or "none")

SOURCES_USED:
(list the sources from search results that you actually used, format: REF_1: Title — URL)

DO NOT hallucinate standards or numbers. If data is insufficient, state it explicitly.
Base confidence on actual evidence quality."""


@tool
def assess_environmental_impact(
    water_description: str = "",
    manure_description: str = "",
    waste_type: str = "both",
    risk_assessment: str = "",
) -> str:
    """
    Assess the environmental impact of poultry wastewater and/or manure at Elmazraa plant.
    Uses real-time web search (Tavily) to fetch current environmental standards (NT 106, WHO, FAO).
    Call this tool after visual analyses are complete, before generating the final report.
    Returns structured environmental compliance assessment with real normative references.
    """
    print("\n[Tool: assess_environmental_impact] Running environmental assessment...")

    water_desc  = (water_description  or "").strip()
    manure_desc = (manure_description or "").strip()
    risk_txt    = (risk_assessment    or "").strip()
    wtype       = (waste_type or "both").strip()

    if not water_desc and not manure_desc:
        return (
            "ENVIRONMENTAL_ASSESSMENT: No visual analysis data provided.\n"
            "Cannot assess environmental impact without sample characterization.\n"
            "CONFIDENCE_ENV:\n- global: 0.0\n- limiting_factor: no_visual_data"
        )

    # Build targeted search query
    env_query = _build_env_query(water_desc, manure_desc, wtype)
    print(f"  [Env] Search query: '{env_query[:100]}...'")

    # Execute Tavily search
    search_results = _tavily_search(env_query, max_results=6)
    formatted_sources = _format_search_results(search_results)
    print(f"  [Env] Got {len(search_results)} sources from Tavily")

    # Extract key VLM indicators for context
    all_text = (water_desc + " " + manure_desc).lower()
    indicators_summary = []
    check_patterns = [
        (r'urate.*crystals[:\s]*(\w+)', "urate_crystals"),
        (r'egg.like[:\s]*(\w+)',        "parasite_structures"),
        (r'bacterial.*clusters[:\s]*(\w+)', "bacteria"),
        (r'hyphae[:\s]*(\w+)',          "fungal_hyphae"),
        (r'turbidity.*level[:\s]*(\w+)', "turbidity"),
        (r'dominant[:\s]+(\w+)',         "color"),
        (r'cell_count_estimate[:\s]*(\w+)', "cell_density"),
    ]
    for pattern, label in check_patterns:
        m = re.search(pattern, all_text)
        if m:
            val = m.group(1).strip()
            if val not in ("not_visible", "absent", "none", "no"):
                indicators_summary.append(f"{label}={val}")

    indicators_str = ", ".join(indicators_summary) if indicators_summary else "standard poultry waste"

    # Build LLM prompt
    prompt = f"""Environmental assessment for Elmazraa poultry plant (Tunisia).
Waste type analyzed: {wtype.upper()}
Date: April 2026 | Season: Spring

KEY VISUAL INDICATORS DETECTED:
{indicators_str}

WATER SAMPLE ANALYSIS:
{water_desc[:1500] if water_desc else "Not analyzed this session."}

MANURE SAMPLE ANALYSIS:
{manure_desc[:1500] if manure_desc else "Not analyzed this session."}

MICROBIOLOGICAL RISK CONTEXT:
{risk_txt[:800] if risk_txt else "Not yet assessed."}

WEB SEARCH RESULTS — ENVIRONMENTAL STANDARDS:
{formatted_sources}

{INTERPRETER_PROMPT}"""

    print("  → LLM generating environmental assessment...")
    result = call_llm(prompt, temperature=0.05, max_tokens=2500)

    if not result or "ERROR" in result or len(result.strip()) < 100:
        # Fallback minimal assessment
        print("  ⚠ LLM failed → fallback assessment")
        fallback = (
            f"ENVIRONMENTAL_RISK_LEVEL:\n- level: medium\n"
            f"- justification: Unable to fully assess — visual indicators suggest standard poultry waste risks.\n\n"
            f"SOIL_IMPACT:\n- nitrogen_leaching_risk: medium\n- phosphorus_runoff_risk: medium\n"
            f"- pathogen_soil_contamination: medium\n- evidence: {indicators_str}\n\n"
            f"WATER_IMPACT:\n- groundwater_contamination_risk: medium\n"
            f"- surface_water_risk: medium\n- aquifer_threat: uncertain\n- evidence: insufficient data\n\n"
            f"AIR_IMPACT:\n- ammonia_emission_risk: medium\n- bioaerosol_risk: low\n- odor_nuisance: medium\n\n"
            f"BIODIVERSITY_IMPACT:\n- flora_risk: low\n- fauna_risk: low\n- ecosystem_disruption: low\n\n"
            f"COMPLIANCE_STATUS:\n- NT_106_002_Tunisia: needs_treatment\n- WHO_2006_guidelines: needs_treatment\n"
            f"- key_limits_exceeded: assessment_incomplete\n- certification_possible: yes_after_treatment\n\n"
            f"MITIGATION_MEASURES:\n- immediate: Apply standard WHO poultry waste treatment protocol\n"
            f"- short_term: Obtain NT 106.002 compliance certification\n"
            f"- long_term: Implement ISO 14001 environmental management system\n"
            f"- estimated_cost_tnd: insufficient data\n\n"
            f"ENVIRONMENTAL_OPPORTUNITIES:\n- carbon_credits_potential: investigate\n"
            f"- green_certification_eligible: conditional\n- circular_economy_score: 5\n\n"
            f"CONFIDENCE_ENV:\n- global: 0.30\n- limiting_factor: llm_generation_failed\n\n"
            f"SOURCES_USED:\nGeneral WHO/NT106 standards applied — no specific sources retrieved."
        )
        return f"ENVIRONMENTAL_ASSESSMENT_COMPLETE\n{'='*50}\n{fallback}"

    print(f"  ✓ Environmental assessment complete ({len(result)} chars)")

    # Append sources block for report extraction
    sources_block = "\nENV_SOURCES_FOR_REPORT:\n"
    for i, r in enumerate(search_results[:6], 1):
        title = r.get("title", "Unknown source")
        url   = r.get("url", "")
        sources_block += f"REF_{i}: {title} — {url}\n"

    full = (
        f"ENVIRONMENTAL_ASSESSMENT_COMPLETE\n"
        f"{'='*50}\n"
        f"SEARCH_QUERY_USED: {env_query}\n"
        f"SOURCES_RETRIEVED: {len(search_results)}\n"
        f"{'='*50}\n"
        f"{result}\n"
        f"{sources_block}"
    )

    return full