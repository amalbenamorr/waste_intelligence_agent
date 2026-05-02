# ============================================================
# state.py — AgentState v14
#
# FIXES vs v13:
# 1. Added xai_cot_trace and xai_attribution for XAI dashboard.
# ============================================================

from typing import TypedDict, Optional, List, Any

class AgentState(TypedDict, total=False):
    # ── Image inputs ─────────────────────────────────────────
    water_image_rgb:    Optional[str]
    water_image_micro:  Optional[str]
    manure_image_rgb:   Optional[str]
    manure_image_micro: Optional[str]

    # ── User request ─────────────────────────────────────────
    user_request: Optional[str]

    # ── Analysis results ─────────────────────────────────────
    water_description:  Optional[str]
    manure_description: Optional[str]
    adaptive_result:    Optional[str]

    # ── Sampling state ───────────────────────────────────────
    sampling_done:       bool
    sampling_confidence: Optional[float]
    images_requested:    List[str]

    # ── Engineer context ─────────────────────────────────────
    context_collected: Optional[str]
    questions_asked:   List[str]

    # ── Tool outputs ─────────────────────────────────────────
    risk_assessment:    Optional[str]
    valorization_plan:  Optional[str]
    roi_result:         Optional[str]
    environmental_impact: Optional[str]

    # ── Long-term memory ─────────────────────────────────────
    past_similar: Optional[Any]

    # ── v11: Short-term session memory ───────────────────────
    session_summary: Optional[str]  # Running summary of current session findings

    # ── Report output ────────────────────────────────────────
    memory_saved: bool
    final_report: Optional[str]
    report_path:  Optional[str]

    # ── Agent control ────────────────────────────────────────
    next_tool:       Optional[str]
    next_args:       dict
    iteration_count: int
    agent_scratchpad: Optional[str]
    error:           Optional[str]

    # ── v13: Direct report injection flag ────────────────────
    # Set to True by should_stop when it directly generates the report
    # and injects it into state, bypassing the empty tool call result.
    # agent_node checks this flag and returns immediately if True.
    _direct_report_injected: Optional[bool]

    # ── v14: XAI (Explainable AI) trace ──────────────────────
    xai_cot_trace:   List[dict]
    xai_attribution: dict

    # ── LangGraph messages ───────────────────────────────────
    messages: List[Any]