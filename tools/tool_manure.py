# ============================================================
# tools/tool_manure.py — Manure Analyzer Tool
# VLM : structured scientific prompt + LLM interpreter
# Anti-hallucination : facts only → interpret separately
# ============================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.tools import tool
from utils.models import call_vlm, call_llm


# ── Prompt VLM RGB ────────────────────────────────────────────
PROMPT_RGB = """You are a visual quality inspector for poultry manure samples.

STRICTLY describe ONLY what you see in this image. No interpretation.

Return ONLY this structure:

COLOR:
- dominant: (brown / yellow / green / white / red / black / mixed)
- secondary: (if any, describe)
- uniformity: (uniform / patchy / streaked)

TEXTURE:
- type: (dry_granular / crumbly / moist_compact / pasty / liquid)
- consistency: (firm / soft / runny)

HUMIDITY:
- level: (very_dry / slightly_dry / moist / wet / liquid)

SURFACE_ANOMALIES:
- mold_patches: (none / small / extensive)
- mold_color: (if present: white / green / black)
- unusual_colors: (none / describe if yes)

VISIBLE_STRUCTURES:
- fibers: (none / few / many)
- feather_fragments: (none / present)
- undigested_particles: (none / few / many)
- other: (describe if any)

OVERALL_CONDITION:
- assessment: (normal / abnormal)
- concern: (none / moisture / mold / color_anomaly / mixed)

IMAGE_QUALITY:
- usable: (yes / no)
- issue: (blur / too_dark / too_bright / none)

DO NOT interpret chemically or biologically. ONLY visual observations."""


# ── Prompt VLM Microscopique ──────────────────────────────────
PROMPT_MICRO = """You are a microbiology visual inspector analyzing a microscopic slide of poultry manure.

STRICTLY describe ONLY what you see. No chemical or biological interpretation.

Return ONLY this structure:

URATE_CRYSTALS:
- presence: (absent / rare / moderate / abundant)
- shape: (geometric_angular / irregular / not_visible)
- color: (white / yellowish / transparent / not_visible)
- size_estimate: (small / medium / large / not_visible)

MICROBIAL_INDICATORS:
- bacterial_clusters: (absent / scattered / dense)
- cluster_morphology: (rod_shaped / round / filamentous / mixed / not_determinable)
- confidence: (low / medium / high)

PARASITES:
- egg_like_structures: (absent / possible / present)
- shape: (oval / round / irregular / not_visible)
- count_estimate: (none / 1-3 / more_than_3)

FUNGAL_ELEMENTS:
- hyphae_filaments: (absent / possible / present)
- spores: (absent / possible / present)

UNDIGESTED_MATTER:
- plant_fibers: (absent / few / many)
- starch_granules: (absent / possible / present)

DENSITY_OVERVIEW:
- overall_density: (sparse / moderate / dense / very_dense)
- dominant_element: (crystals / bacteria / fibers / parasites / unclear)

IMAGE_QUALITY:
- usable: (yes / no)
- focus: (sharp / slightly_blurry / blurry)
- issue: (none / blur / low_contrast / debris_on_slide)

DO NOT interpret chemically. DO NOT assume disease. ONLY visual facts."""


# ── Interpreter LLM ───────────────────────────────────────────
INTERPRETER_PROMPT = """You are a poultry waste biological expert.

You receive structured visual observations from a microscope and/or RGB camera
of a poultry manure (fientes) sample.

Your job: transform visual indicators into biological and agronomic probabilities.

Rules:
- Base ONLY on the visual data provided. Never invent facts.
- If confidence is low → say "inconclusive, image quality insufficient"
- Be specific about valorization potential and health risks

Respond with:

BIOLOGICAL_CONDITION:
- health_status: (normal / mild_concern / concerning / critical)
- evidence: (which visual indicators led to this)
- parasite_risk: (low / medium / high — based on visual only)
- fungal_risk: (low / medium / high)

NITROGEN_CONTENT_ESTIMATE:
- level: (low / medium / high / very_high)
- evidence: (urate crystals density, color, texture)

VALORIZATION_POTENTIAL:
- suitable_for: (compost / biostimulant / biogas / none_yet)
- condition: (ready / needs_treatment_first / not_suitable)
- main_obstacle: (if any)

TREATMENT_RECOMMENDATION:
- urgency: (none / standard / urgent)
- reason: (one clear sentence based on observations only)

CONFIDENCE_SCORE:
- global: (0.0 to 1.0)
- limiting_factor: (what reduced confidence, or "none")

DO NOT hallucinate. If data is insufficient, say it explicitly."""


@tool
def analyze_manure(manure_rgb: str = "", manure_micro: str = "") -> str:
    """
    Analyze poultry manure (fientes) sample images using vision AI.
    Use this tool when manure images are available (RGB path, microscopic path, or both).
    Returns structured visual observations + biological and agronomic interpretation.
    Input: file paths to manure images (empty string if not available).
    Reusable if a new image path is provided that differs from previous analysis.
    """
    print("\n[Tool: analyze_manure] Running...")

    manure_rgb   = (manure_rgb or "").strip().strip('"').strip("'")
    manure_micro = (manure_micro or "").strip().strip('"').strip("'")

    raw_observations = []

    # ── VLM RGB ──
    if manure_rgb and os.path.exists(manure_rgb):
        print("  → VLM analyzing RGB manure image...")
        result = call_vlm(manure_rgb, PROMPT_RGB)
        if result and "ERROR" not in result:
            raw_observations.append(f"[RGB_OBSERVATIONS]\n{result}")
            print(f"  ✓ RGB done")
        else:
            print(f"  ⚠ RGB VLM failed: {result}")
            raw_observations.append("[RGB_OBSERVATIONS]\nIMAGE_QUALITY:\n- usable: no\n- issue: VLM processing error")

    # ── VLM Microscopique ──
    if manure_micro and os.path.exists(manure_micro):
        print("  → VLM analyzing microscopic manure image...")
        result = call_vlm(manure_micro, PROMPT_MICRO)
        if result and "ERROR" not in result:
            raw_observations.append(f"[MICROSCOPIC_OBSERVATIONS]\n{result}")
            print(f"  ✓ Microscopic done")
        else:
            print(f"  ⚠ Micro VLM failed: {result}")
            raw_observations.append("[MICROSCOPIC_OBSERVATIONS]\nIMAGE_QUALITY:\n- usable: no\n- issue: VLM processing error")

    # ── Pas d'images valides ──
    if not raw_observations:
        return (
            "MANURE_ANALYSIS: No valid image paths provided or files not found.\n"
            f"Paths attempted: rgb='{manure_rgb}' micro='{manure_micro}'\n"
            "CONFIDENCE_SCORE:\n- global: 0.0\n- limiting_factor: no_images"
        )

    combined_obs = "\n\n".join(raw_observations)

    # ── Interpreter LLM ──
    print("  → LLM interpreter running...")
    interpretation = call_llm(
        f"Visual observations:\n\n{combined_obs}\n\n{INTERPRETER_PROMPT}",
        temperature=0.05
    )

    if not interpretation or "ERROR" in interpretation:
        return f"RAW_OBSERVATIONS_ONLY (interpreter failed):\n\n{combined_obs}"

    final = (
        f"MANURE_ANALYSIS_COMPLETE\n"
        f"{'='*50}\n"
        f"RAW_VISUAL_OBSERVATIONS:\n{combined_obs}\n\n"
        f"{'='*50}\n"
        f"BIOLOGICAL_INTERPRETATION:\n{interpretation}"
    )

    print(f"  ✓ Manure analysis complete")
    return final