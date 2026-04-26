# ============================================================
# tools/tool_report.py — v16
#
# CHANGES vs v15:
# - NEW param: environmental_impact (str) → new section in report
# - _extract_env_findings() → parse structured env assessment
# - _extract_env_references() → pull REF_ from ENV_SOURCES_FOR_REPORT
# - generate_report() signature + prompt updated with env section
# - All v15 logic preserved intact
# ============================================================

import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.tools import tool
from utils.models import call_llm
from datetime import datetime
from pathlib import Path


def _ensure_string(val) -> str:
    if val is None:
        return ""
    if isinstance(val, dict):
        return "\n".join(f"{k}: {v}" for k, v in val.items())
    if isinstance(val, list):
        return "\n".join(str(item) for item in val)
    return str(val).strip()


def _detect_lang(user_request: str) -> str:
    if not user_request:
        return "fr"
    fr_words = ["comment", "est-ce", "normal", "utiliser", "saison", "que", "quoi", "quel",
                "beneficier", "bénéficier", "je", "nous", "peut", "peux", "faire",
                "traitement", "risque", "produit", "fientes", "eau", "analyser",
                "sain", "propre", "est ce", "contient", "bacterie", "bactérie",
                "environnement", "impact", "pollution", "norme"]
    req_lower = user_request.lower()
    fr_count = sum(1 for w in fr_words if w in req_lower)
    return "fr" if fr_count >= 1 else "en"


def _detect_waste_type(water_analysis: str, manure_analysis: str) -> str:
    has_water  = bool(water_analysis  and len(water_analysis.strip())  > 10)
    has_manure = bool(manure_analysis and len(manure_analysis.strip()) > 10)
    if has_water and not has_manure:
        return "water_only"
    elif has_manure and not has_water:
        return "manure_only"
    elif has_water and has_manure:
        return "both"
    return "unknown"


def _extract_raw_observations(analysis_text: str) -> str:
    if not analysis_text:
        return ""
    text = _ensure_string(analysis_text).strip()

    for marker in ["WATER_FULL_ANALYSIS:", "MANURE_FULL_ANALYSIS:"]:
        if marker in text:
            content = text.split(marker)[-1]
            for stop in ["WATER_ANALYSIS_CONFIDENCE=", "MANURE_ANALYSIS_CONFIDENCE=",
                         "ADAPTIVE_ASSESSMENT:", "RISK_ASSESSMENT:", "ROI_CALCULATION:"]:
                if stop in content:
                    content = content.split(stop)[0]
            cleaned = content.strip()
            if len(cleaned) > 10:
                return cleaned[:3000]

    if "RAW_VISUAL_OBSERVATIONS:" in text:
        raw = text.split("RAW_VISUAL_OBSERVATIONS:")[-1]
        for stop in ["BIOLOGICAL_INTERPRETATION:", "="*10,
                     "MANURE_ANALYSIS_COMPLETE", "WATER_ANALYSIS_COMPLETE"]:
            if stop in raw:
                raw = raw.split(stop)[0]
        cleaned = raw.strip()
        if len(cleaned) > 10:
            return cleaned[:2000]

    if "[RGB_OBSERVATIONS]" in text or "[MICROSCOPIC_OBSERVATIONS]" in text:
        parts = []
        for section in ["[RGB_OBSERVATIONS]", "[MICROSCOPIC_OBSERVATIONS]"]:
            if section in text:
                chunk = text.split(section)[-1]
                for stop in ["[RGB_", "[MICRO", "BIOLOGICAL_INTERP", "="*5]:
                    if stop in chunk:
                        chunk = chunk.split(stop)[0]
                parts.append(chunk.strip()[:800])
        if parts:
            return "\n\n".join(parts)

    if "RAW_OBSERVATIONS_ONLY" in text:
        raw = text.split("RAW_OBSERVATIONS_ONLY")[-1]
        if ":" in raw:
            raw = raw.split(":", 1)[-1]
        return raw.strip()[:1500]

    for raw_key in ["WATER_RAW_OBS:", "MANURE_RAW_OBS:"]:
        if raw_key in text:
            raw = text.split(raw_key)[-1]
            for stop in ["WATER_BIO_INTERP:", "MANURE_BIO_INTERP:", "WATER_", "MANURE_",
                         "RISK_", "VALO_", "SESSION_"]:
                if stop in raw:
                    raw = raw.split(stop)[0]
            return raw.strip()[:1500]

    return text[:1000]


def _extract_bio_interpretation(text: str) -> str:
    if not text:
        return ""
    text = _ensure_string(text).strip()

    if "BIOLOGICAL_INTERPRETATION:" in text:
        bio = text.split("BIOLOGICAL_INTERPRETATION:")[-1].strip()
        return bio[:3000]

    for marker in ["WATER_FULL_ANALYSIS:", "MANURE_FULL_ANALYSIS:"]:
        if marker in text:
            full = text.split(marker)[-1]
            if "BIOLOGICAL_INTERPRETATION:" in full:
                bio = full.split("BIOLOGICAL_INTERPRETATION:")[-1].strip()
                return bio[:3000]

    for bio_key in ["WATER_BIO_INTERP:", "MANURE_BIO_INTERP:"]:
        if bio_key in text:
            bio = text.split(bio_key)[-1]
            for stop in ["WATER_", "MANURE_", "RISK_", "VALO_", "SESSION_"]:
                if stop in bio:
                    bio = bio.split(stop)[0]
            return bio.strip()[:2000]

    bio_fields = ["MICROORGANISM_PRESENCE:", "BIOLOGICAL_CONDITION:", "NITROGEN_LOAD:",
                  "TREATMENT_URGENCY:", "NITROGEN_CONTENT_ESTIMATE:", "VALORIZATION_POTENTIAL:",
                  "ORGANIC_CONTAMINATION:", "CONFIDENCE_SCORE:"]
    for field in bio_fields:
        if field in text:
            idx = text.find(field)
            return text[idx:idx+2500]

    if len(text) > 600:
        return text[-1500:]
    return text


# ── NEW: Environmental impact extraction ─────────────────────

def _extract_env_findings(env_text: str) -> dict:
    """
    v16 NEW: Parse the structured environmental assessment.
    Returns dict with key environmental indicators.
    """
    findings = {
        "risk_level":          "medium",
        "soil_risk":           "medium",
        "water_risk":          "medium",
        "air_risk":            "low",
        "nt106_status":        "needs_treatment",
        "who_status":          "needs_treatment",
        "circular_score":      "5",
        "carbon_credits":      "investigate",
        "green_cert":          "conditional",
        "immediate_action":    "",
        "short_term":          "",
        "long_term":           "",
        "cost_estimate":       "insufficient data",
        "confidence":          0.5,
        "sources_count":       0,
        "limits_exceeded":     "none identified",
    }
    if not env_text:
        return findings

    text = _ensure_string(env_text).lower()

    # Risk level
    m = re.search(r'environmental_risk_level[^:]*:\s*\n\s*-\s*level:\s*(\w+)', text)
    if m: findings["risk_level"] = m.group(1)

    # Soil
    m = re.search(r'nitrogen_leaching_risk:\s*(\w+)', text)
    if m: findings["soil_risk"] = m.group(1)

    # Water
    m = re.search(r'groundwater_contamination_risk:\s*(\w+)', text)
    if m: findings["water_risk"] = m.group(1)

    # Air
    m = re.search(r'ammonia_emission_risk:\s*(\w+)', text)
    if m: findings["air_risk"] = m.group(1)

    # Compliance
    m = re.search(r'nt[_\s]106[_\s]002[_\s]tunisia:\s*(\S+)', text)
    if m: findings["nt106_status"] = m.group(1).strip("()")

    m = re.search(r'who[_\s]2006[_\s]guidelines:\s*(\S+)', text)
    if m: findings["who_status"] = m.group(1).strip("()")

    # Circular economy score
    m = re.search(r'circular_economy_score:\s*([0-9]+(?:\.[0-9]+)?)', text)
    if m: findings["circular_score"] = m.group(1)

    # Carbon credits
    m = re.search(r'carbon_credits_potential:\s*(\w+)', text)
    if m: findings["carbon_credits"] = m.group(1)

    # Green cert
    m = re.search(r'green_certification_eligible:\s*(\w+)', text)
    if m: findings["green_cert"] = m.group(1)

    # Actions
    m = re.search(r'immediate:\s*([^\n]+)', text)
    if m: findings["immediate_action"] = m.group(1).strip()[:150]

    m = re.search(r'short_term:\s*([^\n]+)', text)
    if m: findings["short_term"] = m.group(1).strip()[:150]

    m = re.search(r'long_term:\s*([^\n]+)', text)
    if m: findings["long_term"] = m.group(1).strip()[:150]

    m = re.search(r'estimated_cost_tnd:\s*([^\n]+)', text)
    if m: findings["cost_estimate"] = m.group(1).strip()[:80]

    m = re.search(r'key_limits_exceeded:\s*([^\n]+)', text)
    if m: findings["limits_exceeded"] = m.group(1).strip()[:120]

    # Confidence
    m = re.search(r'confidence_env[^:]*:\s*\n\s*-\s*global:\s*([0-9.]+)', text)
    if m:
        try: findings["confidence"] = float(m.group(1))
        except: pass

    # Sources count
    sources = re.findall(r'REF_\d+:', env_text)
    findings["sources_count"] = len(sources)

    return findings


def _extract_env_references(env_text: str) -> list:
    """
    v16 NEW: Extract references from ENV_SOURCES_FOR_REPORT block.
    """
    if not env_text:
        return []
    refs = []
    seen = set()

    if "ENV_SOURCES_FOR_REPORT:" in env_text:
        block = env_text.split("ENV_SOURCES_FOR_REPORT:")[-1].strip()
        for m in re.finditer(r'REF_\d+:\s*([^\n—]+?)(?:\s*—\s*(https?://[^\s\n]+))?', block):
            title = m.group(1).strip()
            url = m.group(2).strip() if m.group(2) else ""
            ref = f"- [ENV] {title}" + (f": {url}" if url else "")
            if ref not in seen:
                seen.add(ref)
                refs.append(ref)

    # Also catch inline REF_ lines
    for m in re.finditer(r'REF_\d+:\s*([^\n—]+?)(?:\s*—\s*(https?://[^\s\n]+))?', env_text):
        title = m.group(1).strip()
        url = m.group(2).strip() if m.group(2) else ""
        ref = f"- [ENV] {title}" + (f": {url}" if url else "")
        if ref not in seen and len(title) > 5:
            seen.add(ref)
            refs.append(ref)

    return refs[:6]


# ─────────────────────────────────────────────────────────────

def _extract_references_from_text(text: str) -> list:
    if not text:
        return []
    text = _ensure_string(text)
    refs = []
    seen = set()

    for block_marker in ["RISK_SOURCES_FOR_REPORT:", "VALO_SOURCES_FOR_REPORT:"]:
        if block_marker in text:
            block = text.split(block_marker)[-1].strip()
            for stop in ["VALORIZATION_PLAN:", "ROI_CALCULATION:", "ENGINEER_CONTEXT:",
                         "SESSION_MEMORY:", "SAMPLES_ANALYZED:"]:
                if stop in block:
                    block = block.split(stop)[0]
            for m in re.finditer(r'REF_\d+:[^\n]+', block):
                ref_line = m.group(0).strip()
                if ref_line not in seen:
                    seen.add(ref_line)
                    parts = ref_line.split(" — ", 1)
                    if len(parts) == 2:
                        title = parts[0].replace("REF_", "").split(":", 1)[-1].strip()
                        url = parts[1].strip()
                        ref = f"- {title}: {url}"
                    else:
                        ref = f"- {ref_line}"
                    if ref not in seen:
                        seen.add(ref)
                        refs.append(ref)

    for m in re.finditer(r'REF_\d+:\s*([^\n—]+?)(?:\s*—\s*(https?://[^\s\n]+))?', text):
        title = m.group(1).strip()
        url = m.group(2).strip() if m.group(2) else ""
        ref = f"- {title}" + (f": {url}" if url else "")
        if ref not in seen and title not in seen:
            seen.add(ref)
            seen.add(title)
            refs.append(ref)

    for m in re.finditer(r'URL:\s*(https?://[^\s\n]+)', text):
        url = m.group(1).strip()
        before = text[:m.start()]
        title_m = re.search(r'SOURCE:\s*([^\n]+)\s*$', before)
        title = title_m.group(1).strip() if title_m else "Source externe"
        ref = f"- {title}: {url}"
        if ref not in seen:
            seen.add(ref)
            refs.append(ref)

    if "\nSOURCES:\n" in text:
        sources_block = text.split("\nSOURCES:\n")[-1].strip()
        for line in sources_block.split("\n"):
            line = line.strip()
            if line and len(line) > 10 and line not in seen:
                seen.add(line)
                refs.append(f"- {line}" if not line.startswith("-") else line)

    return refs[:10]


def _extract_key_findings(analysis_text: str) -> dict:
    findings = {
        "confidence": 0.5,
        "risk_level": "medium",
        "key_organisms": [],
        "treatment_urgency": "standard",
        "valorization_ready": False,
        "color": "",
        "turbidity": "",
        "texture": "",
        "parasite_detected": False,
        "parasite_detail": "",
        "crystal_detail": "",
        "bacteria_detail": "",
        "fungal_detected": False,
    }
    if not analysis_text:
        return findings

    analysis_text = _ensure_string(analysis_text)

    conf_found = False
    for m in re.findall(r'(?:global|confidence)[:\s=]+([0-9.]+)', analysis_text, re.IGNORECASE):
        try:
            val = float(m)
            if 0.0 <= val <= 1.0:
                findings["confidence"] = val
                conf_found = True
                break
        except ValueError:
            pass

    text_lower = analysis_text.lower()

    for conf_marker in ["WATER_ANALYSIS_CONFIDENCE=", "MANURE_ANALYSIS_CONFIDENCE="]:
        if conf_marker in analysis_text:
            m = re.search(conf_marker + r'([0-9.]+)', analysis_text)
            if m:
                try:
                    val = float(m.group(1))
                    if 0.0 <= val <= 1.0:
                        findings["confidence"] = val
                        conf_found = True
                        break
                except ValueError:
                    pass

    strong_indicators = [
        "urate_crystals.*abundant", "abundant.*urate",
        "egg.like.*present", "egg-like.*present",
        "bacteria.*dense", "dense.*cluster",
        "parasite.*present", "present.*parasite",
        "hyphae.*present", "spore.*present",
        "ANALYSIS_COMPLETE",
        "oval.*structure", "double.*wall", "coccidian",
        "cell_count_estimate.*abundant",
        "particle_density.*very_high",
    ]
    has_strong = any(re.search(p, text_lower) for p in strong_indicators)
    if not conf_found and has_strong:
        findings["confidence"] = 0.72

    if not conf_found and not has_strong:
        visual_indicators = [
            r'dominant.*(?:yellow|brown|grey|black|orange)',
            r'turbidity.*(?:cloudy|opaque)',
            r'sediment_visible.*yes',
            r'shapes_observed.*(?:rod|cocci|filament|mixed)',
            r'clustering.*(?:colonies|dense)',
        ]
        visual_count = sum(1 for p in visual_indicators if re.search(p, text_lower))
        if visual_count >= 3:   findings["confidence"] = 0.62
        elif visual_count >= 2: findings["confidence"] = 0.58
        elif visual_count >= 1: findings["confidence"] = 0.54

    color_match = re.search(r'dominant[:\s]+([a-z_/]+)', text_lower)
    if color_match:
        findings["color"] = color_match.group(1)

    turbidity_match = re.search(r'(?:level|turbidity)[:\s]+([a-z_/]+)', text_lower)
    if turbidity_match:
        findings["turbidity"] = turbidity_match.group(1)

    texture_match = re.search(r'type[:\s]+([a-z_/]+)', text_lower)
    if texture_match:
        findings["texture"] = texture_match.group(1)

    parasite_patterns = [
        r'egg.like.structures[:\s]*([^\n]+)',
        r'parasite[_\s]like[_\s]structures[:\s]*([^\n]+)',
        r'parasite[s]?[:\s]*([^\n]+)',
        r'oval.*structur[^\n]*',
    ]
    for p in parasite_patterns:
        m = re.search(p, text_lower)
        if m:
            val = m.group(0)
            if any(k in val for k in ["present", "possible", "yes", "more_than"]):
                findings["parasite_detected"] = True
                findings["parasite_detail"] = val[:120]
                break

    for p in [r'urate[_\s]crystals[:\s]*([^\n]+)', r'crystal[s]?[:\s]*([^\n]+)']:
        m = re.search(p, text_lower)
        if m:
            findings["crystal_detail"] = m.group(0)[:120]
            break

    for p in [r'bacterial[_\s]clusters[:\s]*([^\n]+)', r'cluster[_\s]morphology[:\s]*([^\n]+)',
              r'bacteria[:\s]*([^\n]+)']:
        m = re.search(p, text_lower)
        if m:
            findings["bacteria_detail"] = m.group(0)[:120]
            break

    organism_keywords = [
        "salmonella", "campylobacter", "e. coli", "escherichia", "clostridium",
        "aspergillus", "fusarium", "coccidia", "eimeria", "ascaris",
        "cryptosporidium", "giardia", "listeria",
        "urate", "crystal", "nitrogen", "azote", "parasit", "fungi", "fungal",
        "bacteria", "bactéri", "hyphae", "spore", "biofilm",
        "fat_globule", "foam", "sediment",
    ]
    for kw in organism_keywords:
        if kw in text_lower:
            findings["key_organisms"].append(kw)

    if "fungi" in text_lower or "fungal" in text_lower or "hyphae" in text_lower:
        findings["fungal_detected"] = True

    if "urgent" in text_lower or "critical" in text_lower:
        findings["treatment_urgency"] = "urgent"
    elif "none" in text_lower and "treatment_urgency" in text_lower:
        findings["treatment_urgency"] = "none"

    if any(k in text_lower for k in ["suitable_for", "ready", "compost", "biostimulant", "fertigation"]):
        findings["valorization_ready"] = True

    return findings


def _requires_mandatory_treatment(water_findings: dict, manure_findings: dict,
                                   water_analysis: str, manure_analysis: str) -> dict:
    all_text = _ensure_string(water_analysis) + _ensure_string(manure_analysis)
    text_lower = all_text.lower()

    treatment_info = {
        "required": False,
        "urgency": "none",
        "pathogens": [],
        "protocol": "",
        "safety_condition": "Pas de traitement préalable requis selon les données disponibles.",
    }

    if water_findings.get("parasite_detected") or manure_findings.get("parasite_detected"):
        treatment_info["required"] = True
        treatment_info["urgency"] = "urgent"
        treatment_info["pathogens"].append("parasites (œufs / structures ovales détectés visuellement)")
        treatment_info["protocol"] += (
            "TRAITEMENT OBLIGATOIRE — PARASITES DÉTECTÉS:\n"
            "→ Compostage thermophile : 55-65°C pendant minimum 15 jours (fientes)\n"
            "→ Traitement UV ou désinfection chimique (eau usée)\n"
            "→ Vérification : absence d'œufs de parasites (analyse lab)\n"
            "→ NE PAS utiliser les déchets bruts directement\n"
        )
        treatment_info["safety_condition"] = (
            "⚠️ DÉCHETS NON SÛRS EN L'ÉTAT — Parasites détectés visuellement. "
            "Traitement obligatoire avant toute valorisation."
        )

    if re.search(r'bacteria.*dense|dense.*cluster|bacterial.*cluster.*dense', text_lower):
        treatment_info["required"] = True
        urgency_order = ["none", "standard", "urgent"]
        current_idx = urgency_order.index(treatment_info["urgency"])
        treatment_info["urgency"] = urgency_order[max(current_idx, urgency_order.index("standard"))]
        treatment_info["pathogens"].append("clusters bactériens denses")
        treatment_info["protocol"] += (
            "TRAITEMENT BACTÉRIEN:\n"
            "→ Option A: Chloration à 5 mg/L Cl résiduel — 30 min contact\n"
            "→ Option B: Traitement thermique 70°C — 30 min\n"
            "→ Conforme WHO 2006 + NT 106.002\n"
        )

    if water_findings.get("fungal_detected") or manure_findings.get("fungal_detected"):
        treatment_info["required"] = True
        treatment_info["pathogens"].append("éléments fongiques (hyphes/spores)")
        treatment_info["protocol"] += (
            "TRAITEMENT ANTIFONGIQUE:\n"
            "→ Compostage avec aération forcée + température > 55°C (fientes)\n"
            "→ Filtration + désinfection pour eau avec éléments fongiques\n"
            "→ Durée minimum 21 jours pour destruction spores\n"
        )

    return treatment_info


@tool
def generate_report(
    water_analysis:       str = "",
    manure_analysis:      str = "",
    risk_assessment:      str = "",
    valorization_plan:    str = "",
    roi_result:           str = "",
    environmental_impact: str = "",   # ← NEW v16
    context:              str = "",
    user_request:         str = ""
) -> str:
    """
    Generate a complete professional waste analysis report for Elmazraa plant.
    Call this as the LAST tool after all analyses are complete.
    Saves the report to disk and returns the full content.
    This tool should be called only once per session.
    ALL parameters must be plain strings.
    NEW in v16: environmental_impact parameter adds environmental compliance section.
    """
    print(f"\n[Tool: generate_report v16] Generating professional report...")

    # Garantir que tous les params sont des strings
    water_analysis       = _ensure_string(water_analysis)
    manure_analysis      = _ensure_string(manure_analysis)
    risk_assessment      = _ensure_string(risk_assessment)
    valorization_plan    = _ensure_string(valorization_plan)
    roi_result           = _ensure_string(roi_result)
    environmental_impact = _ensure_string(environmental_impact)   # ← NEW v16
    context              = _ensure_string(context)
    user_request         = _ensure_string(user_request)

    print(f"  [Params v16] water={len(water_analysis)} manure={len(manure_analysis)} "
          f"risk={len(risk_assessment)} valo={len(valorization_plan)} roi={len(roi_result)} "
          f"env={len(environmental_impact)}")

    def clean(s: str, max_chars: int) -> str:
        s = (s or "").strip()
        return s[:max_chars] + "..." if len(s) > max_chars else s

    lang = _detect_lang(user_request)
    lang_instruction = "Réponds en français professionnel." if lang == "fr" else "Write in professional English."

    waste_type = _detect_waste_type(water_analysis, manure_analysis)

    has_water  = bool(water_analysis  and len(water_analysis.strip())  > 10)
    has_manure = bool(manure_analysis and len(manure_analysis.strip()) > 10)
    has_risk   = bool(risk_assessment and len(risk_assessment.strip()) > 10)
    has_valo   = bool(valorization_plan and len(valorization_plan.strip()) > 10)
    has_roi    = bool(roi_result and len(roi_result.strip()) > 5)
    has_env    = bool(environmental_impact and len(environmental_impact.strip()) > 10)  # ← NEW

    print(f"  [Data v16] water={has_water} manure={has_manure} risk={has_risk} "
          f"valo={has_valo} roi={has_roi} env={has_env} waste_type={waste_type}")

    analyzed = []
    if has_water:  analyzed.append("eaux usées (wastewater)")
    if has_manure: analyzed.append("fientes (manure)")
    scope = " et ".join(analyzed) if analyzed else ("analyse de risques et valorisation" if (has_risk or has_valo) else "analyse générale")

    print(f"  → Scope: {scope} | Lang: {lang} | WasteType: {waste_type} | Env: {has_env}")

    water_raw  = _extract_raw_observations(water_analysis)  if has_water  else ""
    manure_raw = _extract_raw_observations(manure_analysis) if has_manure else ""
    water_bio  = _extract_bio_interpretation(water_analysis)  if has_water  else ""
    manure_bio = _extract_bio_interpretation(manure_analysis) if has_manure else ""

    print(f"  [Extracted v16] water_raw={len(water_raw)} water_bio={len(water_bio)}")
    print(f"  [Extracted v16] manure_raw={len(manure_raw)} manure_bio={len(manure_bio)}")

    water_findings  = _extract_key_findings(water_analysis)
    manure_findings = _extract_key_findings(manure_analysis)

    # ── NEW v16: Extract environmental findings ───────────────
    env_findings = _extract_env_findings(environmental_impact) if has_env else {}
    env_refs     = _extract_env_references(environmental_impact) if has_env else []
    # ─────────────────────────────────────────────────────────

    risk_refs    = _extract_references_from_text(risk_assessment)
    valo_refs    = _extract_references_from_text(valorization_plan)
    roi_refs     = _extract_references_from_text(roi_result)
    context_refs = _extract_references_from_text(context)

    all_refs_list = risk_refs.copy()
    for r in (valo_refs + roi_refs + context_refs + env_refs):   # ← added env_refs
        if r not in all_refs_list:
            all_refs_list.append(r)
    all_refs = "\n".join(all_refs_list[:14]) if all_refs_list else "Aucune référence externe cette session."

    print(f"  [Refs v16] risk={len(risk_refs)} valo={len(valo_refs)} roi={len(roi_refs)} env={len(env_refs)} total={len(all_refs_list)}")

    treatment_info = _requires_mandatory_treatment(
        water_findings, manure_findings, water_analysis, manure_analysis
    )
    if treatment_info["required"]:
        print(f"  [v16 SAFETY] Treatment REQUIRED — pathogens: {treatment_info['pathogens']}")

    req_lower = (user_request or "").lower()
    is_risk_question  = any(k in req_lower for k in ["risque", "risk", "danger", "contamin", "pathogène"])
    is_valo_question  = any(k in req_lower for k in ["valoris", "benefici", "utiliser", "produit",
                                                       "normal", "peut-on", "peut on", "utilisable",
                                                       "comment", "saison", "quoi faire", "que faire",
                                                       "sain", "propre"])
    is_treatment_q    = any(k in req_lower for k in ["traitement", "treatment", "traiter", "comment traiter"])
    is_roi_question   = any(k in req_lower for k in ["roi", "revenu", "profit", "tnd", "économi"])
    is_env_question   = any(k in req_lower for k in ["environnement", "environment", "impact", "pollution",
                                                       "norme", "nt 106", "conformit", "sol", "eau souterraine"])
    is_full_analysis  = not any([is_risk_question, is_valo_question, is_treatment_q, is_roi_question, is_env_question])

    # Always include environmental section
    if treatment_info["required"]:
        sections_needed = ["executive", "observations", "risks", "valorization", "economics", "environment", "nextsteps"]
    elif is_full_analysis:
        sections_needed = ["executive", "observations", "risks", "valorization", "economics", "environment", "nextsteps"]
    elif is_env_question:
        sections_needed = ["executive", "observations", "environment", "nextsteps"]
    elif is_risk_question or is_treatment_q:
        sections_needed = ["executive", "observations", "risks", "environment", "nextsteps"]
    elif is_valo_question:
        sections_needed = ["executive", "observations", "valorization", "economics", "environment", "nextsteps"]
    elif is_roi_question:
        sections_needed = ["executive", "economics", "environment", "nextsteps"]
    else:
        sections_needed = ["executive", "observations", "risks", "valorization", "economics", "environment", "nextsteps"]

    # ── Blocs données ─────────────────────────────────────────
    water_block = ""
    if has_water:
        water_detail_lines = []
        if water_findings["color"]:
            water_detail_lines.append(f"- Couleur dominante: {water_findings['color']}")
        if water_findings["turbidity"]:
            water_detail_lines.append(f"- Turbidité: {water_findings['turbidity']}")
        if water_findings["parasite_detail"]:
            water_detail_lines.append(f"- PARASITES DÉTECTÉS: {water_findings['parasite_detail']}")
        if water_findings["crystal_detail"]:
            water_detail_lines.append(f"- Cristaux/Urate: {water_findings['crystal_detail']}")
        if water_findings["bacteria_detail"]:
            water_detail_lines.append(f"- Bactéries: {water_findings['bacteria_detail']}")
        if water_findings["key_organisms"]:
            water_detail_lines.append(f"- Indicateurs: {', '.join(set(water_findings['key_organisms']))}")
        water_detail_str = "\n".join(water_detail_lines) if water_detail_lines else "Voir analyse complète ci-dessous"

        water_block = f"""[EAU — OBSERVATIONS BRUTES VLM]:
{water_raw[:2000] if water_raw else "Non disponible"}

[EAU — INTERPRÉTATION BIOLOGIQUE]:
{water_bio[:2000] if water_bio else "Non disponible"}

[EAU — DONNÉES CLÉS EXTRAITES v16]:
- Confidence: {water_findings['confidence']:.2f}
{water_detail_str}
- Urgence traitement: {water_findings['treatment_urgency']}
- Type déchet: EAU USÉE (valorisation → engrais liquide / fertigation, PAS compost)
- Traitement obligatoire: {'OUI — ' + ', '.join(treatment_info['pathogens']) if treatment_info['required'] else 'Non requis selon données visuelles'}"""
    else:
        water_block = "[EAU]: Non analysée lors de cette session (aucune image fournie)."

    manure_block = ""
    if has_manure:
        manure_detail_lines = []
        if manure_findings["texture"]:
            manure_detail_lines.append(f"- Texture: {manure_findings['texture']}")
        if manure_findings["color"]:
            manure_detail_lines.append(f"- Couleur: {manure_findings['color']}")
        if manure_findings["parasite_detail"]:
            manure_detail_lines.append(f"- PARASITES DÉTECTÉS: {manure_findings['parasite_detail']}")
        if manure_findings["crystal_detail"]:
            manure_detail_lines.append(f"- Cristaux d'urate: {manure_findings['crystal_detail']}")
        if manure_findings["bacteria_detail"]:
            manure_detail_lines.append(f"- Bactéries: {manure_findings['bacteria_detail']}")
        if manure_findings["key_organisms"]:
            manure_detail_lines.append(f"- Indicateurs: {', '.join(set(manure_findings['key_organisms']))}")
        manure_detail_str = "\n".join(manure_detail_lines) if manure_detail_lines else "Voir analyse complète ci-dessous"

        manure_block = f"""[FIENTES — OBSERVATIONS BRUTES VLM]:
{manure_raw[:2000] if manure_raw else "Non disponible"}

[FIENTES — INTERPRÉTATION BIOLOGIQUE]:
{manure_bio[:2000] if manure_bio else "Non disponible"}

[FIENTES — DONNÉES CLÉS EXTRAITES v16]:
- Confidence: {manure_findings['confidence']:.2f}
{manure_detail_str}
- Urgence traitement: {manure_findings['treatment_urgency']}
- Type déchet: FIENTES SOLIDES (valorisation → compost / biostimulant)
- Traitement obligatoire: {'OUI — ' + ', '.join(treatment_info['pathogens']) if treatment_info['required'] else 'Non requis selon données visuelles'}"""
    else:
        manure_block = "[FIENTES]: Non analysées lors de cette session (aucune image fournie)."

    risk_block = clean(risk_assessment, 1800) if has_risk else "Non évalué cette session."
    valo_block = clean(valorization_plan, 1800) if has_valo else "Non déterminé cette session."

    if has_roi:
        roi_block = clean(roi_result, 1200)
    elif has_valo:
        roi_estimate = ""
        rev_match = re.search(r'(?:weekly|mensuel|revenue)[^\n]*?([0-9][0-9,. ]+TND)', valorization_plan, re.IGNORECASE)
        if rev_match:
            roi_estimate = f"Estimation extraite du plan de valorisation: ~{rev_match.group(1)}/semaine"
        roi_block = roi_estimate if roi_estimate else "ROI non calculé cette session — voir plan de valorisation pour estimations."
    else:
        roi_block = "Non calculé cette session."

    ctx_block = clean(context, 400) if context else "Aucun contexte additionnel."

    # ── NEW v16: Environmental block ─────────────────────────
    env_block = ""
    if has_env:
        ef = env_findings
        env_block = f"""[IMPACT ENVIRONNEMENTAL — ÉVALUATION COMPLÈTE]:
{clean(environmental_impact, 2500)}

[IMPACT ENVIRONNEMENTAL — DONNÉES CLÉS EXTRAITES v16]:
- Niveau de risque environnemental: {ef.get('risk_level', 'medium').upper()}
- Risque sol (lixiviation azote): {ef.get('soil_risk', 'medium')}
- Risque eau (contamination nappe): {ef.get('water_risk', 'medium')}
- Risque air (émissions ammoniac): {ef.get('air_risk', 'low')}
- Statut NT 106.002 Tunisie: {ef.get('nt106_status', 'needs_treatment')}
- Statut WHO 2006: {ef.get('who_status', 'needs_treatment')}
- Paramètres dépassés: {ef.get('limits_exceeded', 'none identified')}
- Score économie circulaire: {ef.get('circular_score', '5')}/10
- Potentiel crédits carbone: {ef.get('carbon_credits', 'investigate')}
- Certification verte éligible: {ef.get('green_cert', 'conditional')}
- Action immédiate: {ef.get('immediate_action', 'voir évaluation complète')}
- Coût estimé mesures: {ef.get('cost_estimate', 'insufficient data')} TND
- Confiance évaluation: {ef.get('confidence', 0.5):.2f}
- Sources consultées: {ef.get('sources_count', 0)}"""
    else:
        env_block = "[IMPACT ENVIRONNEMENTAL]: Non évalué cette session (outil non appelé)."
    # ─────────────────────────────────────────────────────────

    treatment_block = ""
    if treatment_info["required"]:
        treatment_block = f"""
[⚠️ TRAITEMENT OBLIGATOIRE DÉTECTÉ]:
Statut sécurité: {treatment_info['safety_condition']}
Pathogènes identifiés visuellement: {', '.join(treatment_info['pathogens'])}
Urgence: {treatment_info['urgency']}
Protocole:
{treatment_info['protocol']}
RÈGLE: La section valorisation DOIT mentionner ce traitement AVANT toute utilisation.
"""

    if waste_type == "water_only":
        valo_rule = """
RÈGLE VALORISATION EAU USÉE:
- L'eau usée de volaille NE PEUT PAS devenir du compost
- Valorisation eau → engrais liquide organique / fertigation / irrigation agricole
- Produit recommandé: effluent traité pour irrigation / engrais liquide dilué / biostimulant liquide
"""
    elif waste_type == "manure_only":
        valo_rule = """
RÈGLE VALORISATION FIENTES:
- Fientes = déchets SOLIDES → compost / biostimulant azoté / pellets / biogas
- Cristaux d'urate abondants → haute teneur azote → biostimulant ou engrais organique N
"""
    else:
        valo_rule = """
RÈGLE VALORISATION (EAU + FIENTES):
- EAU USÉE → engrais liquide / fertigation / irrigation (jamais "compost")
- FIENTES → compost / biostimulant / pellets
"""

    # ── Section prompts ───────────────────────────────────────
    section_prompts = {
        "executive": f"""
**1. RÉPONSE DIRECTE / EXECUTIVE SUMMARY**
{'─'*40}
Première phrase = réponse DIRECTE à: "{clean(user_request, 200) or 'Analyse complète'}"
- Niveau de confiance RÉEL (eau: {water_findings['confidence']:.2f}, fientes: {manure_findings['confidence']:.2f})
- Niveau de risque global + recommandation prioritaire
- Niveau de risque ENVIRONNEMENTAL: {env_findings.get('risk_level', 'non évalué').upper() if has_env else 'non évalué'}
{"- IMPORTANT: Traitement REQUIS avant valorisation." if treatment_info["required"] else ""}
- WASTE TYPE = {waste_type.upper()}
""",
        "observations": f"""
**2. OBSERVATIONS VISUELLES DÉTAILLÉES**
{'─'*40}
Cite les VRAIES valeurs depuis les blocs [EAU — DONNÉES CLÉS EXTRAITES] et [FIENTES — DONNÉES CLÉS EXTRAITES].
Pour chaque échantillon analysé:
- Couleur/apparence exacte, turbidité/texture, structures biologiques, anomalies
- Niveau de confiance RÉEL
NE PAS écrire "non disponible" si les données sont dans les blocs VLM.
""",
        "risks": f"""
**3. ÉVALUATION DES RISQUES ET TRAITEMENTS**
{'─'*40}
{"⚠️ Pathogènes détectés visuellement — section OBLIGATOIRE." if treatment_info["required"] else ""}
- Niveau de risque avec justification visuelle précise
- Traitement spécifique: {treatment_info['protocol'] if treatment_info['required'] else "Voir risk assessment"}
- Normes WHO + NT 106 tunisienne
""",
        "valorization": f"""
**4. RECOMMANDATIONS DE VALORISATION**
{'─'*40}
{valo_rule}
{"⚠️ Traitement DOIT précéder valorisation." if treatment_info["required"] else ""}
- Produit recommandé ADAPTÉ AU TYPE DE DÉCHET ({waste_type})
- Saison actuelle: {datetime.now().strftime('%B %Y')} — cultures cibles tunisiennes
- Protocole étape par étape
""",
        "economics": f"""
**5. IMPACT ÉCONOMIQUE**
{'─'*40}
Basé sur [CALCUL ROI]:
{roi_block}
- Projections hebdomadaires et mensuelles en TND
{"- Note: ROI conditionnel au traitement préalable." if treatment_info["required"] else ""}
""",

        # ── NEW v16 environmental section ─────────────────────
        "environment": f"""
**6. IMPACT ENVIRONNEMENTAL ET CONFORMITÉ RÉGLEMENTAIRE**
{'─'*40}
{"Basé sur évaluation dynamique Tavily — normes réelles consultées." if has_env else "Évaluation non disponible cette session."}

SOUS-SECTIONS OBLIGATOIRES:
A) RISQUES PAR COMPARTIMENT (sol, eau, air, biodiversité):
   - Citer les niveaux exacts depuis [IMPACT ENVIRONNEMENTAL — DONNÉES CLÉS EXTRAITES]
   - Justifier avec les indicateurs VLM réels (urate, turbidité, bactéries, parasites...)
   - Risque sol: {env_findings.get('soil_risk', 'à évaluer') if has_env else 'non évalué'}
   - Risque eau souterraine: {env_findings.get('water_risk', 'à évaluer') if has_env else 'non évalué'}
   - Risque air (NH3): {env_findings.get('air_risk', 'à évaluer') if has_env else 'non évalué'}

B) CONFORMITÉ NORMATIVE (citer les seuils réels si disponibles):
   - NT 106.002 Tunisie: {env_findings.get('nt106_status', 'à vérifier') if has_env else 'non évalué'}
   - WHO 2006 Guidelines for Safe Use of Wastewater: {env_findings.get('who_status', 'à vérifier') if has_env else 'non évalué'}
   - FAO organic waste standards
   - Paramètres potentiellement dépassés: {env_findings.get('limits_exceeded', 'none') if has_env else 'non évalué'}

C) MESURES DE MITIGATION (concrètes, datées):
   - Action immédiate (aujourd'hui): {env_findings.get('immediate_action', 'appliquer protocole standard') if has_env else 'voir rapport'}
   - Court terme (1 mois): {env_findings.get('short_term', '') if has_env else 'voir rapport'}
   - Long terme (6 mois): {env_findings.get('long_term', '') if has_env else 'voir rapport'}
   - Coût estimé: {env_findings.get('cost_estimate', 'insufficient data') if has_env else 'non calculé'} TND

D) OPPORTUNITÉS ENVIRONNEMENTALES:
   - Score économie circulaire: {env_findings.get('circular_score', 'N/A') if has_env else 'N/A'}/10
   - Potentiel crédits carbone: {env_findings.get('carbon_credits', 'investigate') if has_env else 'investigate'}
   - Certification verte (ISO 14001 / label bio): {env_findings.get('green_cert', 'conditional') if has_env else 'conditional'}
   - Valoriser la conformité comme avantage commercial pour Elmazraa
""",
        # ─────────────────────────────────────────────────────

        "nextsteps": f"""
**7. PROCHAINES ÉTAPES**
{'─'*40}
{"1. AUJOURD'HUI — URGENT: " + treatment_info['protocol'].split(chr(10))[0] if treatment_info["required"] else "1. Aujourd'hui: [action immédiate basée sur les données]"}
2. Cette semaine: [action concrète incluant mesures environnementales]
3. Ce mois: [objectif mesurable — conformité NT 106]
4. Suivi: [indicateur de succès environnemental + agronomique]

**RÉFÉRENCES SOURCES**
{'─'*40}
{all_refs}
""",
    }

    sections_text = "\n".join(section_prompts[s] for s in sections_needed)

    prompt = f"""Tu es consultant senior en agronomie, environnement et conformité réglementaire pour Elmazraa (Tunisie).
{lang_instruction}

Date: {datetime.now().strftime('%Y-%m-%d %H:%M')} | Scope: {scope} | Waste type: {waste_type}
Type de demande: {"ANALYSE COMPLÈTE" if is_full_analysis else "QUESTION SPÉCIFIQUE: " + clean(user_request, 100)}

{'='*50}
DONNÉES RÉELLES DISPONIBLES v16
{'='*50}

{water_block}

{manure_block}

{treatment_block}

[RISQUES IDENTIFIÉS]:
{risk_block}

[PLAN DE VALORISATION]:
{valo_block}

[CALCUL ROI]:
{roi_block}

{env_block}

[CONTEXTE / MÉMOIRE]:
{ctx_block}

[RÉFÉRENCES SOURCES DISPONIBLES — À CITER]:
{all_refs}

{'='*50}
{valo_rule}
{'='*50}

SECTIONS À RÉDIGER
{'='*50}
{sections_text}

RÈGLES ABSOLUES v16:
1. JAMAIS écrire "non analysé" si le bloc [EAU] ou [FIENTES] contient des données VLM
2. Les observations DOIVENT citer les valeurs EXACTES des blocs VLM
3. Si PARASITES DÉTECTÉS → section risques OBLIGATOIRE avec traitement thermique
4. La valorisation DOIT correspondre au type de déchet: eau→engrais liquide, fientes→compost
5. La confiance = valeur RÉELLE
6. CITER les références URL dans la section sources
7. EAU USÉE ≠ COMPOST — ne jamais recommander du compost pour de l'eau usée
8. NEW v16: Section environnement OBLIGATOIRE — citer les normes NT 106.002, WHO 2006, FAO
9. NEW v16: Les risques environnementaux DOIVENT être reliés aux indicateurs VLM réels
10. NEW v16: Toujours mentionner le score économie circulaire et les opportunités certifications"""

    report = call_llm(prompt, temperature=0.1, max_tokens=4000)

    if not report or len(report.strip()) < 300 or "ERROR" in report:
        print(f"  ⚠ LLM report insufficient → fallback")
        report = _build_fallback_report(
            water_bio, manure_bio, water_raw, manure_raw,
            risk_assessment, valorization_plan, roi_result,
            user_request, scope, all_refs,
            water_findings, manure_findings, treatment_info, waste_type,
            environmental_impact, env_findings
        )

    # Post-check
    if has_water or has_manure:
        report_lower = report.lower()
        if "non analysé" in report_lower or "not analyzed" in report_lower:
            print("  ⚠ [v16] Report contains 'non analysé' → appending data")
            report += _build_data_appendix(water_raw, manure_raw, water_bio, manure_bio)

    if waste_type == "water_only" and "compost" in report.lower():
        report += (
            "\n\n[NOTE DE CORRECTION AUTOMATIQUE v16]: "
            "Le rapport mentionne 'compost' pour de l'eau usée. "
            "Pour les eaux usées de volaille, la valorisation correcte est : "
            "engrais liquide organique / fertigation / irrigation agricole traitée."
        )

    path_dir = Path(os.getenv("REPORTS_PATH", "./outputs/reports"))
    path_dir.mkdir(parents=True, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = path_dir / f"report_{ts}.txt"

    env_summary = ""
    if has_env and env_findings:
        env_summary = f"Env risk: {env_findings.get('risk_level','?')} | NT106: {env_findings.get('nt106_status','?')} | CircularScore: {env_findings.get('circular_score','?')}/10\n"

    header = (
        f"ELMAZRAA WASTE INTELLIGENCE REPORT\n"
        f"{'='*60}\n"
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Scope: {scope}\n"
        f"Waste type: {waste_type}\n"
        f"Request: {clean(user_request, 100) or 'Full analysis'}\n"
        f"Confidence: eau={water_findings['confidence']:.2f} | fientes={manure_findings['confidence']:.2f}\n"
        f"{env_summary}"
        f"References: {len(all_refs_list)} sources found\n"
        f"{'='*60}\n\n"
    )
    full_report = header + report

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(full_report)
        print(f"  ✓ Report saved: {path}")
    except Exception as e:
        print(f"  ⚠ Could not save: {e}")

    return f"REPORT_GENERATED\nPath: {path}\n\n{full_report}"


def _build_data_appendix(water_raw, manure_raw, water_bio, manure_bio) -> str:
    parts = ["\n\n--- DONNÉES VLM RÉELLES (ANNEXE) ---"]
    if water_raw:
        parts.append(f"\nEAU — OBSERVATIONS BRUTES VLM:\n{water_raw[:800]}")
    if water_bio:
        parts.append(f"\nEAU — INTERPRÉTATION BIOLOGIQUE:\n{water_bio[:800]}")
    if manure_raw:
        parts.append(f"\nFIENTES — OBSERVATIONS BRUTES VLM:\n{manure_raw[:800]}")
    if manure_bio:
        parts.append(f"\nFIENTES — INTERPRÉTATION BIOLOGIQUE:\n{manure_bio[:800]}")
    return "\n".join(parts) if len(parts) > 1 else ""


def _build_fallback_report(water_bio, manure_bio, water_raw, manure_raw,
                            risk, valo, roi, request, scope, refs,
                            water_findings=None, manure_findings=None,
                            treatment_info=None, waste_type="unknown",
                            environmental_impact=None, env_findings=None):
    q_answer = f"Réponse à '{request[:100]}': Voir données ci-dessous." if request else ""
    wf = water_findings or {}
    mf = manure_findings or {}
    ti = treatment_info or {"required": False, "pathogens": [], "protocol": ""}
    ef = env_findings or {}

    lines = [
        f"RÉPONSE DIRECTE: {q_answer}",
        "",
        "**1. RÉSUMÉ EXÉCUTIF**",
        f"   Scope: {scope} | Type: {waste_type}",
    ]
    if isinstance(wf.get('confidence'), float):
        lines.append(f"   Confiance eau: {wf['confidence']:.2f} | Confiance fientes: {mf.get('confidence', 0.0):.2f}")
    if ti.get("required"):
        lines.append(f"\n   ⚠️ TRAITEMENT OBLIGATOIRE: {', '.join(ti['pathogens'])}")

    lines.append("\n**2. OBSERVATIONS VISUELLES DÉTAILLÉES**")
    if water_raw:
        lines.append(f"   EAU (observations VLM brutes):\n{water_raw[:800]}")
    if water_bio:
        lines.append(f"   EAU (interprétation biologique):\n{water_bio[:800]}")
    if not water_raw and not water_bio:
        lines.append("   EAU: Non fournie cette session.")
    if manure_raw:
        lines.append(f"   FIENTES (observations VLM brutes):\n{manure_raw[:800]}")
    if manure_bio:
        lines.append(f"   FIENTES (interprétation biologique):\n{manure_bio[:800]}")
    if not manure_raw and not manure_bio:
        lines.append("   FIENTES: Non fournies cette session.")

    if ti.get("required"):
        lines += ["", "**3. ÉVALUATION DES RISQUES — TRAITEMENT OBLIGATOIRE**",
                  f"   {ti['protocol']}", f"   {ti['safety_condition']}"]

    lines += ["", "**4. RECOMMANDATIONS DE VALORISATION**"]
    if waste_type == "water_only":
        lines.append("   TYPE: EAU USÉE → engrais liquide organique / fertigation / irrigation")
    lines.append(f"   {(valo or 'Non déterminé.')[:600]}")

    lines += ["", "**5. IMPACT ÉCONOMIQUE**"]
    if roi and len(roi.strip()) > 5:
        lines.append(f"   {roi[:600]}")
    else:
        lines.append("   ROI non calculé cette session.")

    # ── NEW v16: Environmental fallback section ───────────────
    lines += ["", "**6. IMPACT ENVIRONNEMENTAL ET CONFORMITÉ RÉGLEMENTAIRE**"]
    if ef:
        lines += [
            f"   Niveau de risque environnemental: {ef.get('risk_level', 'medium').upper()}",
            f"   Risque sol (lixiviation azote): {ef.get('soil_risk', 'medium')}",
            f"   Risque eau souterraine: {ef.get('water_risk', 'medium')}",
            f"   Risque air (NH3): {ef.get('air_risk', 'low')}",
            f"   Statut NT 106.002 Tunisie: {ef.get('nt106_status', 'needs_treatment')}",
            f"   Statut WHO 2006: {ef.get('who_status', 'needs_treatment')}",
            f"   Score économie circulaire: {ef.get('circular_score', '5')}/10",
            f"   Potentiel crédits carbone: {ef.get('carbon_credits', 'investigate')}",
            f"   Certification verte éligible: {ef.get('green_cert', 'conditional')}",
            f"   Action immédiate: {ef.get('immediate_action', 'appliquer protocole standard')}",
            f"   Coût estimé mesures: {ef.get('cost_estimate', 'N/A')} TND",
        ]
    elif environmental_impact and len(environmental_impact) > 50:
        lines.append(f"   {environmental_impact[:800]}")
    else:
        lines.append("   Impact environnemental non évalué cette session.")
    # ─────────────────────────────────────────────────────────

    lines += ["", "**7. PROCHAINES ÉTAPES**"]
    if ti.get("required"):
        lines.append("   1. URGENT — Traitement obligatoire avant toute utilisation")
        lines.append("   2. Vérification microbiologique après traitement")
    else:
        lines.append("   1. Appliquer les traitements requis selon les risques identifiés")
    lines += [
        "   2. Mettre en œuvre les mesures de mitigation environnementales",
        "   3. Vérifier conformité NT 106.002 tunisienne",
        "   4. Mettre en place le protocole de valorisation recommandé",
        "   5. Planifier une analyse de suivi dans 2 semaines",
        "",
        f"**RÉFÉRENCES**: {(refs or 'Aucune référence disponible.')}",
    ]
    return "\n".join(lines)