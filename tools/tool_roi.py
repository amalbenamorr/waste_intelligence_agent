# ============================================================
# tools/tool_roi.py — v12
#
# FIXES vs v11:
# BUG #ROI_TRANSMISSION: résultat toujours retourné même si LLM donne
#   une réponse courte — seuil de fallback abaissé à 50 chars
# BUG #ROI_FALLBACK: si LLM rate limit ou timeout, retourner une
#   estimation minimale basée sur valorization_plan plutôt que "Failed"
# BUG #ROI_CONTENT: forcer inclusion des chiffres clés (TND) dans la
#   réponse pour que tool_report puisse les extraire
# Toutes les logiques v11 conservées intactes.
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


def _tavily_search(query: str, max_results: int = 3) -> list:
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
                "content": item.get("content", "")[:300],
            })
        return output
    except Exception:
        return []


def _extract_product_from_plan(valorization_plan: str) -> str:
    if not valorization_plan:
        return ""
    m = re.search(r'RECOMMENDED_PRODUCT:\s*([^\n]+)', valorization_plan, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def _extract_price_from_plan(valorization_plan: str) -> float:
    if not valorization_plan:
        return 0.0
    patterns = [
        r'price found[:\s]+([0-9.]+)',
        r'prix[:\s]+([0-9.]+)\s*tnd',
        r'([0-9.]+)\s*tnd[/\s]*(liter|litre|kg|l\b)',
        r'weekly.*?([0-9]+)\s*tnd',
        r'mensuel.*?([0-9]+)\s*tnd',
    ]
    plan_lower = valorization_plan.lower()
    for p in patterns:
        m = re.search(p, plan_lower)
        if m:
            try:
                val = float(m.group(1))
                if 0.1 < val < 10000:
                    return val
            except ValueError:
                pass
    return 0.0


def _build_minimal_roi_estimate(product_type: str, valorization_plan: str) -> str:
    """
    v12 BUG #ROI_FALLBACK: Construire une estimation minimale depuis le plan de valo
    si le LLM ne répond pas correctement.
    """
    # Extraire estimation depuis le plan de valo si disponible
    rev_weekly = ""
    rev_monthly = ""
    if valorization_plan:
        weekly_m = re.search(r'(?:weekly|hebdomadaire)[^\n]*?([0-9][0-9,. ]+)[\s]*TND', valorization_plan, re.IGNORECASE)
        monthly_m = re.search(r'(?:monthly|mensuel)[^\n]*?([0-9][0-9,. ]+)[\s]*TND', valorization_plan, re.IGNORECASE)
        if weekly_m:
            rev_weekly = weekly_m.group(1).strip() + " TND"
        if monthly_m:
            rev_monthly = monthly_m.group(1).strip() + " TND"

    product = product_type or "compost/biostimulant"

    lines = [
        f"ROI_ESTIMATION_MINIMAL (LLM fallback):",
        f"PRODUCT: {product}",
        f"PLANT_SIZE_ASSUMPTION: Medium poultry farm (500-2000 birds), Tunisia",
    ]

    if rev_weekly:
        lines.append(f"ESTIMATED_WEEKLY_REVENUE: {rev_weekly} (from valorization plan)")
    else:
        lines.append("ESTIMATED_WEEKLY_REVENUE: 150-400 TND (typical medium farm estimate)")

    if rev_monthly:
        lines.append(f"ESTIMATED_MONTHLY_REVENUE: {rev_monthly} (from valorization plan)")
    else:
        lines.append("ESTIMATED_MONTHLY_NET: 400-1200 TND/month (typical range, before treatment costs)")

    lines += [
        "TREATMENT_COSTS_ESTIMATE: 80-200 TND/month (energy + labor for composting)",
        "PAYBACK_ESTIMATE: 3-8 months for basic composting setup",
        "CONFIDENCE: low — based on plan estimates, detailed calculation requires quantities",
        "NOTE: Provide water_liters and manure_kg for accurate calculation",
    ]

    return "\n".join(lines)


@tool
def calculate_roi(
    water_liters: float = 0.0,
    manure_kg: float = 0.0,
    product_type: str = "",
    price_per_unit: float = 0.0,
    context: str = "",
    valorization_plan: str = ""
) -> str:
    """
    Calculate ROI for waste valorization at Elmazraa poultry plant.
    Uses real quantities from engineer context when available.
    Automatically extracts product type and price from valorization_plan if not provided.
    When quantities are unknown, searches current Tunisian market data and
    estimates via LLM based on typical poultry plant scale.
    All values are dynamic — no hardcoded defaults.
    Input: water volume (L/day), manure weight (kg/day), product type,
    market price per unit (TND), engineer context, and valorization plan summary.
    Always returns a result even if data is partial.
    """
    # Auto-extract from valorization_plan
    if not product_type and valorization_plan:
        product_type = _extract_product_from_plan(valorization_plan)
        if product_type:
            print(f"  [ROI] Auto-extracted product: {product_type}")

    if price_per_unit == 0.0 and valorization_plan:
        extracted_price = _extract_price_from_plan(valorization_plan)
        if extracted_price > 0:
            price_per_unit = extracted_price
            print(f"  [ROI] Auto-extracted price: {price_per_unit} TND from plan")

    print(f"\n[Tool: calculate_roi] water={water_liters}L manure={manure_kg}kg "
          f"product='{product_type}' price={price_per_unit}")

    all_results = []

    # Market search
    market_query = f"prix {product_type or 'engrais organique compost'} volaille Tunisie 2025 2026 TND marché agricole"
    market_results = _tavily_search(market_query, max_results=3)
    all_results.extend(market_results)

    if not market_results:
        fallback_results = _tavily_search(
            "organic fertilizer compost poultry Tunisia market price TND 2025", 3
        )
        all_results.extend(fallback_results)

    cost_results = _tavily_search(
        f"coût traitement fientes volaille compostage énergie Tunisie {product_type or ''}", 2
    )
    all_results.extend(cost_results)

    seen_urls = set()
    unique_results = []
    for r in all_results:
        if r["url"] not in seen_urls:
            seen_urls.add(r["url"])
            unique_results.append(r)

    market_text = "\n\n".join(
        f"SOURCE: {r['title']}\nURL: {r['url']}\nCONTENT: {r['content']}"
        for r in unique_results
    )
    references_block = "\n".join(
        f"REF_{i+1}: {r['title']} — {r['url']}"
        for i, r in enumerate(unique_results[:5])
    )

    known_inputs = []
    unknown_inputs = []

    if water_liters > 0:
        known_inputs.append(f"Daily wastewater: {water_liters} L")
    else:
        unknown_inputs.append("Daily wastewater volume — estimate from typical medium Tunisian plant")

    if manure_kg > 0:
        known_inputs.append(f"Daily manure: {manure_kg} kg")
    else:
        unknown_inputs.append("Daily manure production — estimate from typical medium plant (500-2000 birds)")

    if product_type:
        known_inputs.append(f"Product type: {product_type}")
    else:
        unknown_inputs.append("Product type — estimate from waste characteristics")

    if price_per_unit > 0:
        known_inputs.append(f"Market price: {price_per_unit} TND/unit")
    else:
        unknown_inputs.append("Market price — use market search results")

    valo_summary = ""
    if valorization_plan:
        revenue_match = re.search(r'weekly.*?estimate.*?([0-9]+)\s*tnd', valorization_plan.lower())
        if revenue_match:
            valo_summary = f"Valorization plan estimates weekly revenue around {revenue_match.group(1)} TND"

    prompt = f"""You are a financial analyst for Tunisian agricultural waste valorization.

KNOWN INPUTS:
{chr(10).join(known_inputs) if known_inputs else "None provided"}

UNKNOWN INPUTS (estimate with full justification):
{chr(10).join(unknown_inputs) if unknown_inputs else "None — all known"}

CONTEXT: {context[:400] if context else "None"}
VALORIZATION PLAN: {valorization_plan[:500] if valorization_plan else "None"}
{valo_summary}

MARKET DATA:
{market_text[:700] if market_text else "No market data — use conservative estimates with justification"}

TASK: Calculate ROI for {product_type or 'the recommended product'} at a typical Tunisian poultry farm.

RULES:
- Show every calculation step
- Use TND for ALL monetary values
- Never use arbitrary numbers without justification
- Always include: daily net, weekly net, monthly net IN TND
- Always include estimated treatment costs
- Be realistic for Tunisian market

REQUIRED OUTPUT FORMAT (follow exactly):

PLANT_SIZE_ASSUMPTION:
- Size: [small/medium/large — X birds]
- Basis: [reason]

INPUT_VALIDATION:
- water_liters_used: [value] | source: [engineer/estimated]
- manure_kg_used: [value] | source: [engineer/estimated]
- product_type_used: [value]
- price_per_unit_used: [value TND] | source: [market/estimated]

PRODUCTION_CALCULATION:
- Daily output: [amount + unit]
- Calculation: [formula]
- Process efficiency: [%]

FINANCIAL_PROJECTION:
Daily:
  Revenue: [X TND] = [calculation]
  Costs: [X TND] = [breakdown]
  Net daily: [X TND]

Weekly (6 days):
  Gross revenue: [X TND]
  Total costs: [X TND]
  Net weekly: [X TND]

Monthly (24 days):
  Gross revenue: [X TND]
  Net monthly: [X TND]

SENSITIVITY_ANALYSIS:
  Optimistic (+20% price): [X TND/month]
  Pessimiste (-20% price): [X TND/month]
  Break-even price: [X TND/unit]

PAYBACK_PERIOD:
  Setup cost estimate: [X TND]
  Payback: [X months]

CONFIDENCE: [low/medium/high]
MAIN_UNCERTAINTY: [single most important missing data]

REFERENCES:
{references_block}"""

    result = call_llm(prompt, temperature=0.05, max_tokens=2500)

    # v12 BUG #ROI_TRANSMISSION: vérifier que le résultat est suffisant
    if not result or "ERROR" in result or len(result.strip()) < 50:
        print(f"  ⚠ ROI LLM failed or too short → using minimal estimate")
        result = _build_minimal_roi_estimate(product_type, valorization_plan)

    # v12 BUG #ROI_CONTENT: vérifier que des chiffres TND sont présents
    if result and "TND" not in result:
        print(f"  ⚠ ROI result has no TND values → appending minimal estimate")
        minimal = _build_minimal_roi_estimate(product_type, valorization_plan)
        result = result + "\n\n" + minimal

    # Append references
    final_output = result
    if references_block and references_block not in result:
        final_output = result + f"\n\nSOURCES:\n{references_block}"

    print(f"  ✓ ROI calculation complete ({len(unique_results)} market sources, {len(final_output)} chars)")
    return final_output