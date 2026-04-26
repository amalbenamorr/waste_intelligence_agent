# ============================================================
# tools/tool_water.py — Wastewater Analyzer Tool
# VLM : structured scientific prompt + LLM interpreter
# Anti-hallucination : facts only → interpret separately
# ============================================================

import sys, os
from typing import Optional
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.tools import tool
from utils.models import call_vlm, call_llm


# ── Prompt VLM RGB ────────────────────────────────────────────
PROMPT_RGB = """You are a visual quality inspector for wastewater samples.

STRICTLY describe ONLY what you see in this image. No interpretation.

Return ONLY this structure:

COLOR:
- dominant: (clear / yellow / brown / orange / red / black / grey)
- intensity: (light / medium / dark)

TURBIDITY:
- level: (transparent / slightly_cloudy / cloudy / opaque)

SURFACE:
- foam: (none / thin_layer / thick)
- oil_film: (none / present)
- floating_particles: (none / few / many)

DEPOSITS:
- sediment_visible: (yes / no)
- sediment_color: (if yes: describe color)
- chunks: (none / few / many)

OVERALL_LOAD:
- visual_density: (light / medium / heavy / very_heavy)

IMAGE_QUALITY:
- usable: (yes / no)
- issue: (blur / too_dark / too_bright / none)

DO NOT interpret chemically. DO NOT guess biological content.
ONLY report what is visually observable."""


# ── Prompt VLM Microscopique ──────────────────────────────────
PROMPT_MICRO = """You are a microbiology visual inspector analyzing a microscopic slide.

STRICTLY describe ONLY what you see. No chemical or biological interpretation.

Return ONLY this structure:

MICROORGANISM_INDICATORS:
- shapes_observed: (rod / cocci / filament / none / mixed)
- clustering: (isolated / pairs / colonies / dense_clusters / none)
- motion_visible: (yes / no / not_determinable)
- cell_count_estimate: (absent / rare / moderate / abundant)
- confidence: (low / medium / high)

NITROGEN_INDICATORS:
- urate_crystals: (absent / rare / moderate / abundant)
- crystal_shape: (geometric / irregular / not_visible)
- crystal_color: (white / yellow / transparent / not_visible)

ORGANIC_LOAD:
- particle_density: (low / medium / high / very_high)
- turbidity_level: (clear / slightly_cloudy / cloudy / opaque)
- biofilm_presence: (yes / no / uncertain)

FAT_INDICATORS:
- fat_globules: (absent / few / many)
- globule_size: (small / medium / large / not_visible)

ANOMALIES:
- parasite_like_structures: (yes / no)
- fungal_elements: (yes / no)
- unidentified_structures: (yes / no — if yes, describe shape briefly)

IMAGE_QUALITY:
- usable: (yes / no)
- magnification_visible: (yes / no)
- issue: (blur / too_dark / out_of_focus / none)

DO NOT interpret chemically. DO NOT assume contamination.
ONLY visual indicators. Confidence must reflect actual clarity."""


# ── Interpreter LLM ───────────────────────────────────────────
INTERPRETER_PROMPT = """You are a biological water quality expert.

You receive structured visual observations from a microscope and/or RGB camera
of a wastewater sample from a poultry plant.

Your job: transform visual indicators into biological probabilities.

Rules:
- Base ONLY on the visual data provided. Never invent facts.
- If confidence is low → say "inconclusive, image quality insufficient"
- Give probability estimates, not certainties
- Be specific about what treatment is likely needed

Respond with:

MICROORGANISM_PRESENCE:
- probability: (low / medium / high / very_high)
- evidence: (what visual indicators support this)
- dominant_type_likely: (bacteria / parasites / fungi / mixed / unknown)

NITROGEN_LOAD:
- level: (low / medium / high / critical)
- evidence: (urate crystals, color, turbidity)

ORGANIC_CONTAMINATION:
- level: (low / medium / high / critical)
- evidence: (particles, biofilm, turbidity)

TREATMENT_URGENCY:
- level: (none / standard / urgent / critical)
- reason: (one clear sentence)

CONFIDENCE_SCORE:
- global: (0.0 to 1.0)
- limiting_factor: (what reduced confidence, or "none")

DO NOT hallucinate. If data is insufficient, say it explicitly."""


@tool
#def analyze_water(water_rgb: str = "", water_micro: str = "") -> str:
def analyze_water(water_rgb: Optional[str] = None, water_micro: Optional[str] = None) -> str:
    """
    Analyze wastewater sample images from a poultry plant using vision AI.
    Use this tool when water images are available (RGB path, microscopic path, or both).
    Returns structured visual observations + biological probability interpretation.
    Input: file paths to water images (empty string if not available).
    Reusable if a new image path is provided that differs from previous analysis.
    """
    print("\n[Tool: analyze_water] Running...")

    # Nettoyer chemins
    water_rgb   = (water_rgb or "").strip().strip('"').strip("'")
    water_micro = (water_micro or "").strip().strip('"').strip("'")

    raw_observations = []

    # ── VLM RGB ──
    if water_rgb and os.path.exists(water_rgb):
        print("  → VLM analyzing RGB water image...")
        result = call_vlm(water_rgb, PROMPT_RGB)
        if result and "ERROR" not in result:
            raw_observations.append(f"[RGB_OBSERVATIONS]\n{result}")
            print(f"  ✓ RGB done")
        else:
            print(f"  ⚠ RGB VLM failed: {result}")
            raw_observations.append("[RGB_OBSERVATIONS]\nIMAGE_QUALITY:\n- usable: no\n- issue: VLM processing error")

    # ── VLM Microscopique ──
    if water_micro and os.path.exists(water_micro):
        print("  → VLM analyzing microscopic water image...")
        result = call_vlm(water_micro, PROMPT_MICRO)
        if result and "ERROR" not in result:
            raw_observations.append(f"[MICROSCOPIC_OBSERVATIONS]\n{result}")
            print(f"  ✓ Microscopic done")
        else:
            print(f"  ⚠ Micro VLM failed: {result}")
            raw_observations.append("[MICROSCOPIC_OBSERVATIONS]\nIMAGE_QUALITY:\n- usable: no\n- issue: VLM processing error")

    # ── Pas d'images valides ──
    if not raw_observations:
        return (
            "WATER_ANALYSIS: No valid image paths provided or files not found.\n"
            f"Paths attempted: rgb='{water_rgb}' micro='{water_micro}'\n"
            "CONFIDENCE_SCORE:\n- global: 0.0\n- limiting_factor: no_images"
        )

    # ── Fusion si deux vues ──
    combined_obs = "\n\n".join(raw_observations)

    # ── Interpreter LLM ──
    print("  → LLM interpreter running...")
    interpretation = call_llm(
        f"Visual observations:\n\n{combined_obs}\n\n{INTERPRETER_PROMPT}",
        temperature=0.05
    )

    if not interpretation or "ERROR" in interpretation:
        # Retourner les observations brutes si l'interprétation échoue
        return f"RAW_OBSERVATIONS_ONLY (interpreter failed):\n\n{combined_obs}"

    final = (
        f"WATER_ANALYSIS_COMPLETE\n"
        f"{'='*50}\n"
        f"RAW_VISUAL_OBSERVATIONS:\n{combined_obs}\n\n"
        f"{'='*50}\n"
        f"BIOLOGICAL_INTERPRETATION:\n{interpretation}"
    )

    print(f"  ✓ Water analysis complete")
    return final