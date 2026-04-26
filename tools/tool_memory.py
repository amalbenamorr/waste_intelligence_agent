# ============================================================
# tools/tool_memory.py — v13
#
# FIXES vs v12:
# 1. Validates that risk and product are meaningful (not "unknown")
#    before saving — warns if called too early before risk+valo tools
# 2. Description enriched automatically from risk + product if short
# 3. All v12 save/count logic preserved
# ============================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.tools import tool


@tool
def save_to_memory(
    description: str,
    risk: str = "unknown",
    product: str = "unknown",
    report_summary: str = ""
) -> str:
    """
    Save current analysis to long-term memory (ChromaDB) for future reference.
    Use this tool once, AFTER search_risk and search_valorization have run,
    so that risk level and recommended product are known and saved accurately.
    Call this BEFORE generating the final report.
    Enables comparison of future analyses with past ones across sessions.
    Input: description of waste findings (use real VLM observations — color, turbidity,
    organisms detected, etc.), risk level found (from search_risk result),
    product recommended (from search_valorization result),
    and a brief summary of the report conclusions.
    The description should contain specific biological/visual details found in the analysis.
    """
    print(f"\n[Tool: save_to_memory] Saving to ChromaDB...")
    print(f"  description len={len(description or '')} risk={risk} product={product}")

    # v13: validate description is meaningful
    description = (description or "").strip()
    if len(description) < 5:
        print("  ⚠ Description too short — using fallback description")
        description = f"Waste analysis session — risk={risk}, product={product}"

    # v13: warn if risk/product still unknown (called too early)
    if risk == "unknown" and product == "unknown":
        print("  ⚠ [v13 WARNING] Both risk and product are 'unknown' — "
              "save_to_memory may have been called before search_risk/search_valorization. "
              "Proceeding anyway but memory quality will be low.")

    # v13: enrich description if it lacks specifics
    risk    = (risk    or "unknown").strip()[:50]
    product = (product or "unknown").strip()[:100]
    report_summary = (report_summary or "").strip()[:500]

    # Auto-enrich description with risk/product if they are real
    if risk != "unknown" and risk not in description.lower():
        description = f"{description} | risk_level={risk}"
    if product != "unknown" and product not in description.lower():
        description = f"{description} | product={product}"

    try:
        from memory.long_term import LongTermMemory
        mem = LongTermMemory()

        # v12: verify ChromaDB count before and after
        count_before = mem.count()
        aid = mem.save(description, risk, product, report_summary)
        count_after = mem.count()

        if count_after > count_before:
            print(f"  ✓ Saved successfully. ID: {aid} | DB count: {count_before} → {count_after}")
            return f"MEMORY_SAVED: ID={aid} | risk={risk} | product={product} | DB_total={count_after}"
        else:
            print(f"  ⚠ Save returned ID but DB count unchanged ({count_before})")
            return f"MEMORY_SAVE_WARNING: ID={aid} | DB count unchanged at {count_before}"

    except Exception as e:
        print(f"  ⚠ Memory save failed: {e}")
        return f"MEMORY_SAVE_FAILED: {e}"