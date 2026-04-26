"""
voice_handler.py v7 — El Mazraa — click_browse_btn + close_modal

FIXES v7 (SEULS changements vs v6):
 1. click_browse_btn ajouté → "clique", "ouvre", "appuie" → déclenche le bouton du browse popup
 2. Exemples NLU enrichis
"""

import os, json, tempfile
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from groq import Groq
from pydantic import BaseModel as _BM
from typing import Optional

router = APIRouter(prefix="/voice", tags=["voice"])
_groq  = Groq(api_key=os.getenv("GROQ_API_KEY"))

# ── Mots-clés silencieux à ignorer ────────────────────────────
_SILENCE = {
    "", "...", "…", ".", "..", "je", "euh", "hm", "hmm", "ah", "oh",
    "bon", "ok", "voila", "voilà", "bien", "merci", "ouais", "oui",
    "non", "donc", "alors", "allez", "allons", "hop", "c'est parti",
    "on y va", "prêt", "ready", "go", "let's go",
}

# Patterns d'hallucination Whisper à rejeter
_HALLUCINATION_PATTERNS = [
    "sous-titres par",
    "subtitles by",
    "transcribed by",
    "jordan c13",
    "telecomushina",
    "juanfrance",
    "patchwork",
    "revitalization to be seen",
    "sofi doll",
    "point, wait, wait",
    "jérémy diaz",
]

def _is_hallucination(text: str) -> bool:
    t = text.lower().strip()
    for p in _HALLUCINATION_PATTERNS:
        if p in t:
            return True
    return False


# ── System prompt NLU ──────────────────────────────────────────
_SYS = """Tu es l'assistant vocal ALEXA d'un dashboard agricole (El Mazraa, Tunisie).
Les employés portent des gants donc ils parlent à la voix.
Tu dois comprendre EXACTEMENT ce qu'ils disent : français, arabe tunisien, franglish.

Tu reçois un JSON "context" (état du dashboard) puis le "transcript" vocal.

== RÈGLES ABSOLUES ==
1. fill_question : la valeur params.value = TOUT le texte de la question, mot pour mot, SANS rien couper.
2. browse_* : dès que l'utilisateur dit "ouvre", "choisir", "sélectionner", "browse", "dossier", "fichier",
   "image", "choisir une image", "ouvrir le dossier", "je veux choisir" → déclencher le browse approprié.
   - "eau RGB" ou "eau" ou "RGB" ou "eau usée" → browse_water_rgb
   - "eau micro" ou "microscopique eau" → browse_water_micro
   - "fientes" ou "manure" ou "fiente RGB" → browse_manure_rgb
   - "fientes micro" ou "microscopique fientes" → browse_manure_micro
   - Si pas précisé → browse_water_rgb par défaut
3. close_modal : dès que l'utilisateur dit "ferme", "ferme la fenêtre", "ferme ça", "ferme le popup",
   "ferme la boîte", "annule", "annuler", "quitte", "cache", "dismiss", "close", "ferme cette fenêtre",
   "barra", "sektou", "ferma" → action="close_modal".
4. scroll : "descends", "monte", "scroll bas/haut", "en bas", "en haut", "fais défiler" → scroll_down / scroll_up.
5. Si context.any_image_filled=false et action=launch_analysis → speech="Aucune image renseignée."
6. Si context.report_ready=false et action=tab_report → speech="Aucun rapport. Lancez d'abord une analyse."
7. Transcripts vides, "...", sons courts, "merci", "ok" seuls → action="noop", speech="", confidence=0.
8. Ne JAMAIS couper ou résumer la valeur d'une question demandée.
9. Répondre UNIQUEMENT en JSON valide. Aucun texte avant ni après.
10. Si l'utilisateur demande l'heure, la date → action="noop", speech="[ta réponse directe en max 15 mots]"

== FORMAT DE RÉPONSE ==
{"action":"<action>","params":{<params>},"speech":"<max 20 mots en français>","confidence":<0.0-1.0>}

== ACTIONS DISPONIBLES ==
tab_report | tab_stats | tab_log
launch_analysis
copy_report | print_report
clear_fields  → params: {} ou {"field":"question|water|manure"}
scroll_up | scroll_down
theme_dark | theme_light
stop_voice
close_modal
click_browse_btn
browse_water_rgb | browse_water_micro | browse_manure_rgb | browse_manure_micro
fill_water_rgb    → params: {"value":"<chemin>"}
fill_water_micro  → params: {"value":"<chemin>"}
fill_manure_rgb   → params: {"value":"<chemin>"}
fill_manure_micro → params: {"value":"<chemin>"}
fill_question     → params: {"value":"<TEXTE COMPLET EXACT de la question>"}
noop | unknown

== EXEMPLES CRITIQUES click_browse_btn ==

transcript: "clique"
→ {"action":"click_browse_btn","params":{},"speech":"","confidence":0.95}

transcript: "clique dessus"
→ {"action":"click_browse_btn","params":{},"speech":"","confidence":0.95}

transcript: "ouvre"
→ {"action":"click_browse_btn","params":{},"speech":"","confidence":0.90}

transcript: "appuie sur le bouton"
→ {"action":"click_browse_btn","params":{},"speech":"","confidence":0.92}

transcript: "clique sur ouvrir"
→ {"action":"click_browse_btn","params":{},"speech":"","confidence":0.95}

transcript: "valide"
→ {"action":"click_browse_btn","params":{},"speech":"","confidence":0.88}

== EXEMPLES CRITIQUES close_modal ==

transcript: "ferme la fenêtre"
→ {"action":"close_modal","params":{},"speech":"Fenêtre fermée.","confidence":0.97}

transcript: "ferme ça"
→ {"action":"close_modal","params":{},"speech":"Fermé.","confidence":0.95}

transcript: "annule"
→ {"action":"close_modal","params":{},"speech":"Annulé.","confidence":0.95}

transcript: "ferme le popup"
→ {"action":"close_modal","params":{},"speech":"Popup fermé.","confidence":0.97}

transcript: "quitte"
→ {"action":"close_modal","params":{},"speech":"Fermé.","confidence":0.92}

transcript: "close"
→ {"action":"close_modal","params":{},"speech":"Fermé.","confidence":0.95}

transcript: "dismiss"
→ {"action":"close_modal","params":{},"speech":"Fermé.","confidence":0.95}

transcript: "ferme cette boîte"
→ {"action":"close_modal","params":{},"speech":"Fenêtre fermée.","confidence":0.95}

transcript: "cache le popup"
→ {"action":"close_modal","params":{},"speech":"Caché.","confidence":0.90}

transcript: "ferma" (arabe tunisien)
→ {"action":"close_modal","params":{},"speech":"Fermé.","confidence":0.85}

== EXEMPLES CRITIQUES browse ==

transcript: "ouvre le dossier pour choisir une image eau"
→ {"action":"browse_water_rgb","params":{},"speech":"Appuyez sur le bouton pour choisir votre image eau.","confidence":0.95}

transcript: "je veux choisir une image"
→ {"action":"browse_water_rgb","params":{},"speech":"Appuyez sur le bouton pour choisir votre image.","confidence":0.88}

transcript: "choisir une image RGB"
→ {"action":"browse_water_rgb","params":{},"speech":"Appuyez sur le bouton pour choisir votre image eau RGB.","confidence":0.92}

transcript: "ouvre le browse eau"
→ {"action":"browse_water_rgb","params":{},"speech":"Appuyez sur le bouton pour choisir votre image.","confidence":0.95}

transcript: "choisir fientes"
→ {"action":"browse_manure_rgb","params":{},"speech":"Appuyez sur le bouton pour choisir votre image fientes.","confidence":0.88}

transcript: "ouvre le dossier"
→ {"action":"browse_water_rgb","params":{},"speech":"Appuyez sur le bouton pour choisir votre image.","confidence":0.85}

== EXEMPLES CRITIQUES scroll ==

transcript: "descends un peu"
→ {"action":"scroll_down","params":{},"speech":"","confidence":0.92}

transcript: "scroll en bas"
→ {"action":"scroll_down","params":{},"speech":"","confidence":0.95}

transcript: "monte en haut"
→ {"action":"scroll_up","params":{},"speech":"","confidence":0.92}

== EXEMPLES CRITIQUES fill_question ==

transcript: "Écris la question Quoi faire dans cette saison avec les fientes ?"
→ {"action":"fill_question","params":{"value":"Quoi faire dans cette saison avec les fientes ?"},"speech":"Question enregistrée.","confidence":0.95}

transcript: "Quel est l'agent qui est activé ?"
→ {"action":"fill_question","params":{"value":"Quel est l'agent qui est activé ?"},"speech":"Question enregistrée.","confidence":0.95}

== AUTRES EXEMPLES ==

transcript: "affiche les statistiques"
→ {"action":"tab_stats","params":{},"speech":"Statistiques","confidence":0.95}

transcript: "ferme le micro"
→ {"action":"stop_voice","params":{},"speech":"Micro fermé.","confidence":0.98}

transcript: "active le mode nuit"
→ {"action":"theme_dark","params":{},"speech":"Mode nuit activé.","confidence":0.92}

transcript: "efface la question"
→ {"action":"clear_fields","params":{"field":"question"},"speech":"Question effacée.","confidence":0.90}

transcript: "quelle heure est-il ?"
→ {"action":"noop","params":{},"speech":"Je ne peux pas afficher l'heure directement.","confidence":0.80}

transcript: "..." ou "" ou "merci" seul ou "ok" seul
→ {"action":"noop","params":{},"speech":"","confidence":0.0}
"""


def _parse(raw: str) -> dict:
    raw = raw.strip()
    for fence in ("```json", "```"):
        if fence in raw:
            parts = raw.split(fence)
            for p in parts:
                p = p.strip()
                if p.startswith("{"):
                    raw = p
                    break
    try:
        return json.loads(raw)
    except Exception:
        s, e = raw.find("{"), raw.rfind("}") + 1
        if s >= 0 and e > s:
            try:
                return json.loads(raw[s:e])
            except Exception:
                pass
    return {"action": "unknown", "params": {}, "speech": "Je n'ai pas compris.", "confidence": 0.1}


def _extract_question_from_transcript(transcript: str) -> str:
    if not transcript:
        return ""
    prefixes_to_strip = [
        "écris la question",
        "écrit la question",
        "note la question",
        "inscris la question",
        "supprime cette question et écrit",
        "supprime cette question et écris",
        "efface cette question et écrit",
        "efface la question et écrit",
        "remplace la question par",
        "mets la question",
        "pose la question",
        "la question est",
    ]
    lower = transcript.lower().strip()
    for prefix in prefixes_to_strip:
        if prefix in lower:
            idx = lower.index(prefix) + len(prefix)
            val = transcript[idx:].strip().strip('«»"\'').strip()
            if val:
                return val
    return transcript.strip()


@router.post("/transcribe")
async def transcribe(
    audio:   UploadFile = File(...),
    context: Optional[str] = Form(default=None),
):
    ctx = {}
    if context:
        try: ctx = json.loads(context)
        except Exception: pass

    suffix = ".webm"
    ct = (audio.content_type or "").lower()
    if "wav" in ct:    suffix = ".wav"
    elif "ogg" in ct:  suffix = ".ogg"
    elif "mp4" in ct:  suffix = ".mp4"
    elif "mpeg" in ct: suffix = ".mp3"

    audio_bytes = await audio.read()
    if not audio_bytes or len(audio_bytes) < 300:
        return JSONResponse({"action":"noop","params":{},"speech":"","confidence":0.0,"transcript":""})

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        with open(tmp_path, "rb") as f:
            transcription = _groq.audio.transcriptions.create(
                file=(f"audio{suffix}", f, ct or "audio/webm"),
                model="whisper-large-v3-turbo",
                language="fr",
                response_format="text",
                prompt="El Mazraa, analyse, eau, fientes, image, RGB, microscopique, rapport, statistiques, scroll, mode nuit, browse, dossier, lancer, bactéries, Tunisie, ouvrir, choisir, ferme, annule, quitte, popup, fenêtre",
            )
        transcript = (transcription or "").strip()
        print(f"  [Voice] Transcript: '{transcript}'")

        norm = transcript.lower().strip(".… \t")
        if norm in _SILENCE or len(norm) < 2:
            return JSONResponse({"action":"noop","params":{},"speech":"","confidence":0.0,"transcript":transcript})

        if _is_hallucination(transcript):
            print(f"  [Voice] Hallucination filtered: '{transcript}'")
            return JSONResponse({"action":"noop","params":{},"speech":"","confidence":0.0,"transcript":transcript})

        user_msg = f"context: {json.dumps(ctx, ensure_ascii=False)}\ntranscript: {transcript}"

        try:
            completion = _groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": _SYS},
                    {"role": "user",   "content": user_msg},
                ],
                temperature=0.02,
                max_tokens=300,
            )
            raw = completion.choices[0].message.content.strip()
        except Exception as groq_err:
            err_str = str(groq_err)
            print(f"  [Voice] Groq NLU error: {err_str[:200]}")
            if "rate_limit" in err_str or "429" in err_str:
                return JSONResponse({
                    "action": "noop",
                    "params": {},
                    "speech": "Limite d'API atteinte. Veuillez patienter.",
                    "confidence": 0.0,
                    "transcript": transcript
                })
            return JSONResponse({
                "action": "noop",
                "params": {},
                "speech": "",
                "confidence": 0.0,
                "transcript": transcript
            })

        print(f"  [Voice] LLaMA raw: {raw}")
        action = _parse(raw)

        if action.get("action") == "noop":
            action["speech"] = action.get("speech", "")

        if action.get("action") == "fill_question":
            val = action.get("params", {}).get("value", "")
            if not val or len(val) < 3:
                val = _extract_question_from_transcript(transcript)
                action.setdefault("params", {})["value"] = val

        action["transcript"] = transcript
        print(f"  [Voice] Action: {action}")
        return JSONResponse(action)

    except Exception as e:
        print(f"  [Voice] Unexpected error: {e}")
        return JSONResponse({
            "action": "noop",
            "params": {},
            "speech": "",
            "confidence": 0.0,
            "transcript": "",
            "error": str(e)[:100]
        })
    finally:
        try:
            import os as _os
            _os.unlink(tmp_path)
        except Exception: pass


@router.get("/health")
async def voice_health():
    return {"status": "ok", "groq_key": bool(os.getenv("GROQ_API_KEY"))}


class _TextReq(_BM):
    text: str
    context: Optional[dict] = None


@router.post("/transcribe_text")
async def transcribe_text(req: _TextReq):
    transcript = req.text.strip()
    if not transcript:
        return JSONResponse({"action":"noop","params":{},"speech":"","confidence":0.0})
    ctx = req.context or {}
    user_msg = f"context: {json.dumps(ctx, ensure_ascii=False)}\ntranscript: {transcript}"
    try:
        completion = _groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": _SYS},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.02,
            max_tokens=300,
        )
        raw    = completion.choices[0].message.content.strip()
        action = _parse(raw)
        if action.get("action") == "fill_question":
            val = action.get("params", {}).get("value", "")
            if not val or len(val) < 3:
                val = _extract_question_from_transcript(transcript)
                action.setdefault("params", {})["value"] = val
        action["transcript"] = transcript
        return JSONResponse(action)
    except Exception as e:
        err_str = str(e)
        if "rate_limit" in err_str or "429" in err_str:
            return JSONResponse({"action":"noop","params":{},"speech":"Limite d'API atteinte.","confidence":0.0,"transcript":transcript})
        return JSONResponse({"action":"unknown","params":{},"speech":"Erreur interne.","confidence":0.0,"transcript":transcript})