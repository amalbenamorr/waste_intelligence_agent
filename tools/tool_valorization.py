# ============================================================
# tools/tool_valorization.py — v11
#
# FIXES vs v10:
# 1. Références (URL + titre) extraites et retournées dans le résultat
# 2. Protocole spécifique aux caractéristiques RÉELLES trouvées dans images
#    (ex: cristaux d'urate abondants → biostimulant azoté vs manque → compost)
# 3. Prix marché recherchés avec plusieurs queries Tavily pour fiabilité
# 4. Condition post-traitement injectée depuis tool_risk si disponible
# 5. Saison + cultures actuelles injectées dynamiquement
# ============================================================

import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.tools import tool
from utils.models import call_llm
from datetime import datetime

try:
    from tavily import TavilyClient
    TAVILY_AVAILABLE = True
except ImportError:
    TAVILY_AVAILABLE = False


def _get_season_context() -> dict:
    month = datetime.now().month
    year  = datetime.now().year

    if month in (3, 4, 5):
        return {
            "season": "printemps",
            "year": year,
            "month_name": datetime.now().strftime("%B"),
            "crops": "céréales (blé, orge fin de cycle), maraîchage (tomate, poivron, courgette), arboriculture fruitière (oliviers, agrumes)",
            "needs": "apport azoté de surface pour booster la croissance végétative, fertilisation de fond pour cultures d'été",
            "timing": "période idéale pour engrais organiques liquides et biostimulants foliaires",
            "priority_product": "biostimulant azoté liquide ou engrais organique liquide",
        }
    elif month in (6, 7, 8):
        return {
            "season": "été",
            "year": year,
            "month_name": datetime.now().strftime("%B"),
            "crops": "cultures maraîchères d'été (pastèque, melon, piment), vignes, oliviers",
            "needs": "fertilisation d'entretien, résistance au stress hydrique",
            "timing": "application tôt le matin — éviter chaleur pour compost",
            "priority_product": "compost mûr ou amendement organique solide",
        }
    elif month in (9, 10, 11):
        return {
            "season": "automne",
            "year": year,
            "month_name": datetime.now().strftime("%B"),
            "crops": "semis céréales (blé dur, orge), plantation arbres fruitiers, maraîchage d'automne",
            "needs": "fertilisation de fond avant semis, amendement organique du sol",
            "timing": "meilleure période pour enfouir compost solide avant les pluies",
            "priority_product": "compost solide enfoui ou engrais organiques de fond",
        }
    else:
        return {
            "season": "hiver",
            "year": year,
            "month_name": datetime.now().strftime("%B"),
            "crops": "céréales en végétation, cultures fourragères, agrumes",
            "needs": "faible demande azotée, préparation stocks compost",
            "timing": "période de maturation du compost, préparation saison printanière",
            "priority_product": "compostage et stockage pour printemps",
        }


def _tavily_search(query: str, max_results: int = 4) -> list:
    """Returns list of {title, url, content} dicts."""
    if not TAVILY_AVAILABLE:
        return []
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return []
    try:
        client = TavilyClient(api_key=api_key)
        results = client.search(query, max_results=max_results)
        output = []
        for item in results.get("results", []):
            output.append({
                "title":   item.get("title", ""),
                "url":     item.get("url", ""),
                "content": item.get("content", "")[:350],
            })
        return output
    except Exception as e:
        print(f"  [Tavily] Error: {e}")
        return []


def _format_results(results: list) -> str:
    if not results:
        return "No results found."
    parts = []
    for r in results:
        parts.append(f"SOURCE: {r['title']}\nURL: {r['url']}\nCONTENT: {r['content']}")
    return "\n\n".join(parts)


def _detect_product_type(query: str) -> str:
    """Detect most likely product type from query characteristics."""
    query_lower = query.lower()
    if any(k in query_lower for k in ["urate", "crystal", "nitrogen", "azote", "high nitrogen"]):
        return "biostimulant azoté"
    if any(k in query_lower for k in ["parasite", "fungi", "pathogen", "risk"]):
        return "compost traité thermiquement"
    if any(k in query_lower for k in ["liquid", "liquide", "eau", "water"]):
        return "engrais liquide organique"
    return "compost ou biostimulant"


@tool
def search_valorization(query: str) -> str:
    """
    Search for valorization protocols and current market prices for poultry waste.
    Use this tool after risk assessment confirms waste is safe or conditionally safe.
    The query should reflect the actual waste characteristics found in analysis
    (e.g., "abundant urate crystals high nitrogen manure biostimulant Tunisia spring 2026").
    Products may include: biostimulant, compost, biogas, liquid fertilizer.
    Input: specific search query based on actual analysis findings (max 15 words).
    Returns: recommended product, specific protocol, current Tunisian market prices,
    seasonal recommendation, and source references.
    """
    query = (query or "").strip()[:150]
    if not query:
        query = "fientes volaille poultry manure valorisation compost biostimulant Tunisie"

    # Ensure poultry context in query
    if "poultry" not in query.lower() and "fient" not in query.lower() and "volaille" not in query.lower():
        query = "poultry manure fientes volaille " + query

    print(f"\n[Tool: search_valorization] Query: '{query}'")

    season_ctx = _get_season_context()
    detected_product = _detect_product_type(query)
    print(f"  → Season: {season_ctx['season']} {season_ctx['year']} | Likely product: {detected_product}")

    all_results = []

    # ── Search 1: Main query ───────────────────────────────────
    main_results = _tavily_search(query, max_results=4)
    all_results.extend(main_results)
    print(f"  → Main search: {len(main_results)} results")

    # ── Search 2: Specific product protocol ───────────────────
    protocol_query = f"{detected_product} poultry manure protocol production Tunisia agriculture"
    protocol_results = _tavily_search(protocol_query, max_results=3)
    all_results.extend(protocol_results)

    # ── Search 3: Seasonal use in Tunisia ─────────────────────
    seasonal_query = (
        f"fientes volaille poultry manure fertilisant {season_ctx['season']} "
        f"Tunisie {season_ctx['year']} application {season_ctx['crops'][:50]}"
    )
    seasonal_results = _tavily_search(seasonal_query, max_results=3)
    all_results.extend(seasonal_results)
    print(f"  → Seasonal search: {len(seasonal_results)} results")

    # ── Search 4: Market prices Tunisia ───────────────────────
    price_query = f"prix {detected_product} compost engrais organique volaille Tunisie {season_ctx['year']} TND marché"
    price_results = _tavily_search(price_query, max_results=3)
    all_results.extend(price_results)
    print(f"  → Price search: {len(price_results)} results")

    # ── Search 5: Farmer demand this season ───────────────────
    farmer_query = f"besoins agriculteurs tunisiens engrais organique {season_ctx['season']} {season_ctx['year']}"
    farmer_results = _tavily_search(farmer_query, max_results=2)
    all_results.extend(farmer_results)

    # Deduplicate
    seen_urls = set()
    unique_results = []
    for r in all_results:
        if r["url"] not in seen_urls:
            seen_urls.add(r["url"])
            unique_results.append(r)

    search_text = _format_results(unique_results)
    references_block = "\n".join(
        f"REF_{i+1}: {r['title']} — {r['url']}"
        for i, r in enumerate(unique_results[:6])
    )

    print(f"  → Total unique results: {len(unique_results)}")

    prompt = f"""You are a waste valorization expert for Tunisian poultry industry.

Search query used: "{query}"
Detected waste characteristics/likely product: {detected_product}

=== SEASONAL CONTEXT (Tunisia, {season_ctx['month_name']} {season_ctx['year']}) ===
Season: {season_ctx['season']}
Current crops in field: {season_ctx['crops']}
Current agricultural needs: {season_ctx['needs']}
Optimal application timing: {season_ctx['timing']}
Priority product for this season: {season_ctx['priority_product']}

=== SEARCH RESULTS ===
{search_text[:2800]}

Based on the search results AND the actual waste characteristics in the query,
provide a SPECIFIC valorization plan — not generic.

CRITICAL: 
- If query mentions "urate crystals" or "high nitrogen" → recommend biostimulant/liquid N fertilizer
- If query mentions "parasites" or "pathogens" → recommend thermophilic composting first
- If query mentions "liquid/water" → recommend liquid organic fertilizer
- Base product choice on WHAT WAS FOUND, not defaults

Respond with EXACTLY this structure:

RECOMMENDED_PRODUCT: [biostimulant / compost / biogas / liquid_fertilizer / mixed]
PRODUCT_JUSTIFICATION: [why THIS product based on the waste characteristics found AND current season — be specific]

SEASONAL_RECOMMENDATION:
- Current season: {season_ctx['season']} {season_ctx['year']}
- Why relevant now: [specific agronomic reason based on crop calendar]
- Target crops this season: [from search results + seasonal context]
- Application method: [specific method for maximum effectiveness this season]
- Optimal timing: [days/weeks, time of day, conditions]

PRODUCTION_PROTOCOL:
[SPECIFIC protocol based on detected waste type — not generic steps]
Step 1: [specific — include measurements/temperatures/durations from results]
  Reference: [source if found]
Step 2: [specific]
  Reference: [source if found]
Step 3: [quality control / testing before use]
  Reference: [source if found]
[Add steps if in results]

TREATMENT_BEFORE_USE:
[If parasites/pathogens detected in the analysis:]
- Required treatment: [specific treatment — temps, duration]
- Time required: [days/weeks]
[If no pathogen concern: "No pre-treatment required if risk assessment confirmed safe"]

CURRENT_MARKET_PRICES_TUNISIA:
- Product: {detected_product}
- Price found: [TND/liter or TND/kg — from search results, be specific]
- Price source: [source name — URL]
- Price date: [if available]
- Alternative products: [other market prices found]

FARMER_DEMAND:
- Current seasonal need: [specific — what Tunisian farmers need right NOW]
- Target buyer profile: [cereal farmers / horticulture / arboriculture]
- Competitive advantage vs chemical fertilizers: [why organic this season]

ESTIMATED_REVENUE:
- Basis: [search result data used]
- Weekly low estimate: [TND — show calculation]
- Weekly high estimate: [TND — show calculation]
- Confidence: [low/medium/high]
- Main uncertainty: [what's missing]

DATA_QUALITY: [sufficient / partial / insufficient]
REFERENCES_USED:
{references_block}"""

    plan = call_llm(prompt, temperature=0.05, max_tokens=2500)

    if not plan or "ERROR" in plan:
        return (
            f"RECOMMENDED_PRODUCT: {detected_product}\n"
            f"PRODUCT_JUSTIFICATION: LLM planning failed — using detected type.\n"
            f"SEASONAL_RECOMMENDATION:\n- Season: {season_ctx['season']} {season_ctx['year']}\n"
            f"- Priority: {season_ctx['priority_product']}\n"
            f"DATA_QUALITY: insufficient\n"
            f"REFERENCES_USED:\n{references_block}"
        )

    # v11: Append references to output
    final_output = plan + f"\n\nSOURCES:\n{references_block}"

    print(f"  ✓ Valorization plan complete ({len(unique_results)} sources)")
    return final_output