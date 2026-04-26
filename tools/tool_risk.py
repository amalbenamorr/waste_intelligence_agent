# ============================================================
# tools/tool_risk.py — v11
#
# FIXES vs v10:
# 1. Query enrichie automatiquement avec les VRAIS indicateurs des analyses
# 2. Traitement SPÉCIFIQUE par pathogène/indicateur (pas générique)
# 3. Références (URL + titre) extraites et retournées dans le résultat
# 4. Norme tunisienne NT 106.002 + NT 106.003 recherchées spécifiquement
# 5. Paramètres WHO (valeurs limites numériques) recherchés et cités
# ============================================================

import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.tools import tool
from utils.models import call_llm

try:
    from tavily import TavilyClient
    TAVILY_AVAILABLE = True
except ImportError:
    TAVILY_AVAILABLE = False


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


def _extract_specific_organisms(query: str) -> list:
    """Extract specific organisms/indicators mentioned in the query."""
    organisms = []
    patterns = [
        "salmonella", "campylobacter", "e.coli", "escherichia", "clostridium",
        "aspergillus", "fusarium", "coccidia", "eimeria", "ascaris",
        "cryptosporidium", "giardia", "listeria",
        "urate", "parasite", "fungi", "fungal", "bacteria",
        "nitrogen", "azote", "crystal", "biofilm",
        "egg", "hyphae", "spore",
    ]
    query_lower = query.lower()
    for p in patterns:
        if p in query_lower:
            organisms.append(p)
    return organisms


@tool
def search_risk(query: str) -> str:
    """
    Search for sanitary risks, WHO norms, and health hazards related to
    poultry wastewater or manure contamination.
    Use this tool to assess biological and chemical risks based on the actual
    visual analysis results. Query should reflect what was found (e.g., specific
    organisms or conditions detected).
    Input: specific search query based on actual analysis findings (max 15 words).
    Returns: RISK_LEVEL, specific pathogens found, treatment protocols per pathogen,
    safety assessment, WHO/NT106 norms with values, and source references.
    """
    query = (query or "").strip()[:150]
    if not query:
        query = "poultry manure Salmonella Campylobacter parasites WHO guidelines treatment"

    print(f"\n[Tool: search_risk] Query: '{query}'")

    # v11: Extract specific organisms to build targeted searches
    found_organisms = _extract_specific_organisms(query)
    print(f"  → Organisms detected in query: {found_organisms or 'none specific'}")

    # ── Search 1: Main query ───────────────────────────────────
    main_results = _tavily_search(query, max_results=4)
    print(f"  → Main search: {len(main_results)} results")

    all_results = list(main_results)

    # ── Search 2: Specific organisms treatment protocols ───────
    if found_organisms:
        orgs_str = " ".join(found_organisms[:3])
        treatment_query = f"treatment {orgs_str} poultry waste disinfection protocol WHO"
        treatment_results = _tavily_search(treatment_query, max_results=3)
        all_results.extend(treatment_results)
        print(f"  → Treatment search: {len(treatment_results)} results")
    else:
        # Generic poultry pathogens if no specific ones
        generic_results = _tavily_search(
            "Salmonella Campylobacter poultry wastewater treatment disinfection WHO limit", 3
        )
        all_results.extend(generic_results)

    # ── Search 3: Tunisian norm NT 106 ─────────────────────────
    nt106_results = _tavily_search(
        "norme tunisienne NT 106 compost fientes volaille microbiologique qualité", 2
    )
    all_results.extend(nt106_results)
    print(f"  → NT106 search: {len(nt106_results)} results")

    # ── Search 4: WHO values for detected organisms ─────────────
    if found_organisms:
        who_query = f"WHO guidelines limit {found_organisms[0]} poultry wastewater reuse irrigation"
        who_results = _tavily_search(who_query, max_results=2)
        all_results.extend(who_results)

    # Deduplicate by URL
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

    prompt = f"""You are a sanitary risk expert for poultry plant waste (Tunisia).

Search query used: "{query}"
Specific organisms/indicators in query: {found_organisms or ['not specified — use generic poultry pathogens']}

Search results:
{search_text[:2500]}

Based ONLY on these search results, provide a structured and specific risk assessment.
Do NOT generalize if specific organisms were found. Be precise about treatment protocols.

Respond with EXACTLY this structure:

RISK_LEVEL: [low / medium / high / critical]
RISK_LEVEL_JUSTIFICATION: [one sentence — cite the specific indicator that drives this level]

BIOLOGICAL_RISKS_IDENTIFIED:
[For each specific organism/indicator found in the analysis, list:]
- [Organism/indicator]: [specific risk it poses] — [source reference if found]
[If no specific organisms, list: "Based on typical poultry waste profile: Salmonella spp., Campylobacter jejuni, Eimeria spp."]

CHEMICAL_RISKS:
- [list or "not identified in available results"]

WHO_STANDARDS:
- [Cite specific WHO limit values if found, e.g.: "WHO 2006: E. coli < 10^3 CFU/100mL for restricted irrigation"]
- [If not found: "WHO guidelines applicable but specific values not retrieved in this search"]

TUNISIAN_NORMS_NT106:
- [Cite NT 106 values if found, e.g.: "NT 106.002: absence of Salmonella in 25g"]
- [If not found: "NT 106 applicable — specific values not retrieved in this search"]

TREATMENT_PROTOCOLS:
[For each identified risk/organism, give a SPECIFIC treatment:]
- For [organism/risk]: [specific treatment — temperature, duration, chemical, dosage if found in results]
  Reference: [source if available]
[Example: "For Salmonella: thermal treatment at 70°C for 30 min OR chlorination at 5 mg/L (WHO, 2004)"]
[Example: "For parasitic eggs (Ascaris/Eimeria): thermophilic composting at 55°C+ for 15 days minimum"]

TREATMENT_REQUIRED: [yes / no / conditional]
TREATMENT_SEQUENCE:
1. [First treatment step — specific]
2. [Second step]
3. [Verification/testing step]

SAFE_FOR_VALORIZATION: [yes / no / conditional_after_treatment]
VALORIZATION_CONDITION: [specific condition — what exactly needs to happen first]

DATA_QUALITY: [sufficient / partial / insufficient]
REFERENCES_USED:
{references_block}"""

    assessment = call_llm(prompt, temperature=0.05, max_tokens=2000)

    if not assessment or "ERROR" in assessment:
        return (
            f"RISK_LEVEL: medium\n"
            f"RISK_LEVEL_JUSTIFICATION: Assessment failed — using conservative default.\n"
            f"BIOLOGICAL_RISKS_IDENTIFIED:\n- Poultry waste typical pathogens: Salmonella, Campylobacter\n"
            f"TREATMENT_REQUIRED: yes\n"
            f"RAW_SEARCH_RESULTS:\n{search_text[:600]}"
        )

    # v11: Append references block to output for use in report
    final_output = assessment + f"\n\nSOURCES:\n{references_block}"

    print(f"  ✓ Risk assessment complete ({len(unique_results)} sources)")
    return final_output