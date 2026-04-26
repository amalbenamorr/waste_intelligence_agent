# ============================================================
# tools/tool_adaptive.py — v12
#
# FIXES vs v11:
# BUG #CONFIDENCE: _extract_confidence élargi — plus de patterns visuels
#   pour éviter le fallback 0.50 quand des indicateurs sont présents
#   → patterns RGB (couleur, turbidité, sédiments) inclus en plus des micro
# BUG #FASTPATH: seuil fast CONTINUE abaissé à 0.54 (au lieu de 0.65)
#   pour couvrir les images RGB without micro qui donnent conf ~0.54-0.62
# BUG #STRONG: patterns renforcés — cell_count_estimate, particle_density,
#   sediment_visible, biofilm_presence ajoutés aux indicateurs forts
# Toutes les logiques v11 conservées intactes.
# ============================================================

import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.tools import tool
from utils.models import call_llm


def _extract_confidence(text: str) -> float:
    """
    v12: Confiance dynamique — couvre patterns RGB + micro.
    Évite le fallback 0.50 quand des indicateurs visuels sont présents.
    """
    if not text:
        return 0.0

    # 1. Valeur numérique explicite
    for m in re.findall(r'(?:global|confidence)[:\s=]+([0-9.]+)', text, re.IGNORECASE):
        try:
            v = float(m)
            if 0.0 <= v <= 1.0:
                return v
        except ValueError:
            pass

    text_lower = text.lower()

    # 2. Labels textuels
    if "confidence: high" in text_lower:    return 0.85
    if "confidence: medium" in text_lower:  return 0.65
    if "confidence: low" in text_lower:     return 0.35
    if "no_images" in text_lower:           return 0.0
    if "not found" in text_lower:           return 0.0

    # 3. ANALYSIS_COMPLETE → confiance élevée
    if "analysis_complete" in text_lower:   return 0.70

    # 4. Indicateurs forts (micro) → confiance élevée
    strong_patterns = [
        r'urate.*abundant|abundant.*urate',
        r'egg.like.*present|present.*egg.like',
        r'bacteria.*dense|dense.*cluster',
        r'parasite.*present|present.*parasite',
        r'hyphae.*present|spore.*present',
        r'oval.*structure.*present',
        r'double.*wall.*structur',
        r'cell_count_estimate.*abundant',
        r'particle_density.*very_high',
        r'biofilm_presence.*yes',
    ]
    strong_count = sum(1 for p in strong_patterns if re.search(p, text_lower))
    if strong_count >= 3: return 0.82
    if strong_count >= 2: return 0.74
    if strong_count >= 1: return 0.66

    # 5. v12 NEW: Indicateurs visuels RGB généraux → confiance modérée
    # Ces patterns sont présents même pour une image RGB seule
    visual_patterns = [
        r'dominant.*(?:yellow|brown|grey|black|orange|clear)',
        r'turbidity.*(?:slightly_cloudy|cloudy|opaque)',
        r'sediment_visible.*yes',
        r'shapes_observed.*(?:rod|cocci|filament|mixed)',
        r'clustering.*(?:colonies|dense|pairs)',
        r'visual_density.*(?:medium|heavy|very_heavy)',
        r'overall_load.*(?:medium|heavy)',
        r'surface.*(?:foam|oil|floating)',
        r'fat_globules.*(?:few|many)',
        r'urate_crystals.*(?:rare|moderate)',
        r'cell_count_estimate.*(?:rare|moderate)',
        r'overall_condition.*(?:abnormal|concerning)',
        r'concern.*(?:moisture|mold|color_anomaly)',
    ]
    visual_count = sum(1 for p in visual_patterns if re.search(p, text_lower))
    if visual_count >= 4: return 0.65
    if visual_count >= 3: return 0.62
    if visual_count >= 2: return 0.58
    if visual_count >= 1: return 0.54

    # 6. Présence de sections structurées → image au moins lisible
    structured = sum(1 for kw in ["COLOR:", "TURBIDITY:", "SURFACE:", "DEPOSITS:",
                                   "IMAGE_QUALITY:", "URATE_CRYSTALS:", "MICROBIAL_INDICATORS:"]
                     if kw in text)
    if structured >= 4: return 0.55
    if structured >= 2: return 0.52

    # 7. Fallback minimal
    return 0.50


def _has_strong_indicators(text: str) -> bool:
    """
    v12: Détecte indicateurs forts + indicateurs RGB généraux.
    Fast-path CONTINUE si l'analyse contient des observations claires.
    """
    if not text:
        return False

    # Indicateurs micro forts (comme v11)
    strong = [
        "urate_crystals.*abundant", "urate.*abundant",
        "abundant.*urate",
        "egg.like.*present", "egg-like.*present",
        "bacteria.*dense", "dense.*cluster",
        "parasite.*present", "present.*parasite",
        "hyphae.*present", "spore.*present",
        "biofilm.*yes", "yes.*biofilm",
        "ANALYSIS_COMPLETE",
        "nitrogen.*high", "high.*nitrogen",
        "organic.*high", "high.*organic",
        "treatment_urgency.*urgent", "urgent.*treatment",
        "cell_count_estimate.*abundant",
        "particle_density.*very_high",
    ]
    text_lower = text.lower()
    for pattern in strong:
        if re.search(pattern, text_lower):
            return True

    # v12 NEW: indicateurs RGB généraux suffisants → CONTINUE aussi
    # Si l'image RGB est claire et structurée, pas besoin de poser des questions
    rgb_sufficient = [
        "visual_density.*heavy",
        "turbidity.*opaque",
        "sediment_visible.*yes",
        r'surface.*foam.*thick',
        "overall_condition.*abnormal",
    ]
    rgb_count = sum(1 for p in rgb_sufficient if re.search(p, text_lower))
    if rgb_count >= 2:
        return True

    # v12 NEW: si l'image est structurée et usable → forte indication que l'analyse est bonne
    sections_found = sum(1 for kw in ["COLOR:", "TURBIDITY:", "SURFACE:", "DEPOSITS:",
                                       "URATE_CRYSTALS:", "MICROBIAL_INDICATORS:",
                                       "PARASITES:", "FUNGAL_ELEMENTS:"]
                         if kw in text)
    if sections_found >= 5:
        return True

    return False


def _image_is_unusable(text: str) -> bool:
    """Check if VLM explicitly said image is unusable."""
    if not text:
        return True
    patterns = ["usable: no", "usable:no", "vlm processing error",
                "image not found", "no valid image", "issue: blur",
                "issue: too_dark", "focus: blurry"]
    text_lower = text.lower()
    return any(p in text_lower for p in patterns)


@tool
def adaptive_intelligence(
    water_description: str = "",
    manure_description: str = "",
    questions_asked: str = "",
    context_so_far: str = "",
    user_request: str = ""
) -> str:
    """
    Evaluate the current analysis quality and decide the next intelligent action.
    Use this tool ONCE after visual analysis to determine if more information is needed.
    It evaluates confidence scores from visual analyses and decides:
    - REQUEST_IMAGE: ONLY if image is explicitly unusable (blur, darkness, VLM error)
    - ASK_QUESTION: ONLY if a specific critical unknown would significantly change recommendations
                    AND confidence is between 0.4 and 0.6
    - CONTINUE: if analysis has confidence >= 0.54 OR strong/sufficient indicators are found
    Never repeat a question already asked. Always returns a CONFIDENCE_SCORE.
    """
    print("\n[Tool: adaptive_intelligence] Running...")

    water_conf  = _extract_confidence(water_description)
    manure_conf = _extract_confidence(manure_description)

    available_analyses = []
    if water_description:
        available_analyses.append(
            f"WATER_ANALYSIS (confidence={water_conf:.2f}):\n"
            f"{water_description[:500]}"
        )
    if manure_description:
        available_analyses.append(
            f"MANURE_ANALYSIS (confidence={manure_conf:.2f}):\n"
            f"{manure_description[:500]}"
        )

    if not available_analyses:
        return (
            "ACTION: CONTINUE\n"
            "REASON: No analyses available yet — proceed with what is provided.\n"
            "CONFIDENCE_SCORE: 0.0\n"
            "CONFIDENCE_ASSESSMENT: Insufficient data, continuing based on request only."
        )

    # v12: Fast-path — strong indicators found → CONTINUE immediately
    all_text = (water_description or "") + (manure_description or "")
    if _has_strong_indicators(all_text):
        max_conf = max(water_conf, manure_conf)
        effective_conf = max(max_conf, 0.65)
        print(f"  → Fast CONTINUE: strong/sufficient indicators detected (conf={effective_conf:.2f})")
        return (
            f"ACTION: CONTINUE\n"
            f"REASON: Analysis contains sufficient biological/visual indicators "
            f"(e.g., structured observations, specific morphology, density indicators). "
            f"Analysis is specific enough to proceed with risk assessment.\n"
            f"CONFIDENCE_SCORE: {effective_conf:.2f}\n"
            f"CONFIDENCE_ASSESSMENT: Analysis contains clear visual observations — sufficient for recommendations."
        )

    # v12: Fast-path — conf >= 0.54 → CONTINUE (abaissé de 0.65)
    max_conf = max(water_conf, manure_conf)
    if max_conf >= 0.54:
        print(f"  → Fast CONTINUE: adequate confidence ({max_conf:.2f})")
        return (
            f"ACTION: CONTINUE\n"
            f"REASON: Analysis confidence is {max_conf:.2f} — adequate to proceed with recommendations.\n"
            f"CONFIDENCE_SCORE: {max_conf:.2f}\n"
            f"CONFIDENCE_ASSESSMENT: Visual observations are sufficiently structured for biological interpretation."
        )

    # Check if image is explicitly unusable → REQUEST_IMAGE
    water_unusable  = _image_is_unusable(water_description)
    manure_unusable = _image_is_unusable(manure_description)

    if (water_unusable and water_description) or (manure_unusable and manure_description):
        sample = "water" if water_unusable else "manure"
        print(f"  → {sample} image is unusable → REQUEST_IMAGE")
        return (
            f"ACTION: REQUEST_IMAGE\n"
            f"INSTRUCTION: Please provide a clearer {sample} sample image. "
            f"Ensure adequate lighting, avoid blur, and keep the sample container clean.\n"
            f"CONFIDENCE_SCORE: {min(water_conf, manure_conf):.2f}\n"
            f"CONFIDENCE_ASSESSMENT: Image quality is insufficient for biological interpretation."
        )

    # Medium confidence (0.4-0.54) → let LLM decide
    already_asked = questions_asked if questions_asked else "None"
    context = context_so_far if context_so_far else "None"
    request = user_request if user_request else "full analysis"

    prompt = f"""You are an adaptive quality controller for a poultry plant waste analysis system.

CURRENT ANALYSES:
{chr(10).join(available_analyses)}

Max confidence: {max_conf:.2f}
Strong indicators found: {_has_strong_indicators(all_text)}
Any image unusable: {water_unusable or manure_unusable}

USER REQUEST: {request}
QUESTIONS ALREADY ASKED: {already_asked}
ENGINEER CONTEXT: {context}

DECISION RULES (v12):
1. If confidence >= 0.54 OR strong indicators → ALWAYS CONTINUE (already handled above if here)
2. If any image is explicitly flagged usable:no → REQUEST_IMAGE
3. If confidence 0.4-0.54 AND a critical question would improve accuracy → ASK_QUESTION
4. If confidence 0.4-0.54 AND no critical question can help → CONTINUE
5. NEVER ask about something already in context
6. NEVER ask vague questions — only critical unknowns
7. For simple yes/no questions from user → be lenient → CONTINUE

Most analyses with structured visual observations already contain enough info → CONTINUE.

Respond with EXACTLY:

ACTION: [CONTINUE]
REASON: [one sentence]
CONFIDENCE_SCORE: [0.0 to 1.0]
CONFIDENCE_ASSESSMENT: [one sentence]

OR:

ACTION: [ASK_QUESTION]
QUESTION: [one specific, critical question]
CONFIDENCE_SCORE: [0.0 to 1.0]
CONFIDENCE_ASSESSMENT: [why this question matters]"""

    response = call_llm(prompt, temperature=0.05)

    if not response or "ERROR" in response:
        return (
            "ACTION: CONTINUE\n"
            "REASON: Adaptive evaluation failed — proceeding with available data.\n"
            f"CONFIDENCE_SCORE: {max_conf:.2f}\n"
            "CONFIDENCE_ASSESSMENT: Evaluation unavailable, proceeding cautiously."
        )

    # Safety override — if LLM somehow says REQUEST_IMAGE but no image is unusable
    if "ACTION: REQUEST_IMAGE" in response and not (water_unusable or manure_unusable):
        print("  → Overriding REQUEST_IMAGE to CONTINUE (images are usable)")
        return (
            f"ACTION: CONTINUE\n"
            f"REASON: Images are usable — proceeding with current analysis quality.\n"
            f"CONFIDENCE_SCORE: {max_conf:.2f}\n"
            f"CONFIDENCE_ASSESSMENT: Analysis quality is adequate for recommendations."
        )

    print(f"  → Decision: {response[:100]}...")
    return response