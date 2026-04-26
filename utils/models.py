import os, base64, requests, time
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

FACULTY_API_BASE = os.getenv("FACULTY_API_BASE", "").rstrip("/")
FACULTY_API_KEY  = os.getenv("FACULTY_API_KEY", "")

GROQ_API_KEY  = os.getenv("GROQ_API_KEY", "")
GROQ_API_BASE = "https://api.groq.com/openai/v1"
LLM_MODEL     = "llama-3.3-70b-versatile"
VLM_MODEL     = "meta-llama/llama-4-scout-17b-16e-instruct"


def call_llm(prompt: str, system: str = None, temperature: float = 0.2, max_tokens: int = 2048) -> str:
    """
    Appel LLM avec retry automatique (3 tentatives, backoff exponentiel).
    Gère silencieusement le rate limit Groq qui tronque les réponses longues.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    last_error = ""
    for attempt in range(3):
        if attempt > 0:
            wait = 2 ** attempt  # 2s, 4s
            print(f"  [LLM] Retry {attempt}/2 after {wait}s (last: {last_error[:60]})")
            time.sleep(wait)
        try:
            r = requests.post(
                f"{GROQ_API_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                         "Content-Type": "application/json"},
                json={"model": LLM_MODEL, "messages": messages,
                      "temperature": temperature, "max_tokens": max_tokens},
                timeout=90
            )
            # Rate limit → wait and retry
            if r.status_code == 429:
                retry_after = int(r.headers.get("retry-after", 5))
                print(f"  [LLM] Rate limit 429 — waiting {retry_after}s...")
                time.sleep(retry_after + 1)
                last_error = "429 rate limit"
                continue

            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"].strip()

            # Réponse trop courte = probable troncature → retry
            if len(content) < 80 and max_tokens > 200:
                last_error = f"response too short ({len(content)} chars)"
                print(f"  [LLM] Response too short ({len(content)} chars) → retry")
                time.sleep(3)
                continue

            return content

        except requests.exceptions.Timeout:
            last_error = "timeout"
        except Exception as e:
            last_error = str(e)

    return f"ERROR: {last_error}"


def call_vlm(image_path: str, prompt: str) -> str:
    p = Path(image_path)
    if not p.exists():
        return f"ERROR: Image not found at {image_path}"
    with open(p, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    media = "image/jpeg" if p.suffix.lower() in [".jpg", ".jpeg"] else "image/png"

    # 1. Essaie Groq Vision d'abord
    if GROQ_API_KEY:
        for attempt in range(2):
            if attempt > 0:
                time.sleep(3)
            try:
                r = requests.post(
                    f"{GROQ_API_BASE}/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                             "Content-Type": "application/json"},
                    json={"model": VLM_MODEL,
                          "messages": [{"role": "user", "content": [
                              {"type": "image_url",
                               "image_url": {"url": f"data:{media};base64,{b64}"}},
                              {"type": "text", "text": prompt}
                          ]}],
                          "temperature": 0.1, "max_tokens": 512},
                    timeout=60
                )
                if r.status_code == 429:
                    retry_after = int(r.headers.get("retry-after", 5))
                    print(f"  [VLM] Rate limit 429 — waiting {retry_after}s...")
                    time.sleep(retry_after + 1)
                    continue
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"].strip()
            except Exception as e:
                print(f"  [VLM] Groq vision failed: {e} → trying Faculty API")
                break

    # 2. Fallback : Faculty API
    if FACULTY_API_BASE and FACULTY_API_KEY:
        try:
            r = requests.post(
                f"{FACULTY_API_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {FACULTY_API_KEY}",
                         "Content-Type": "application/json"},
                json={"model": "hosted_vllm/llava-1.5-7b-hf",
                      "messages": [{"role": "user", "content": [
                          {"type": "image_url",
                           "image_url": {"url": f"data:{media};base64,{b64}"}},
                          {"type": "text", "text": prompt}
                      ]}],
                      "temperature": 0.1, "max_tokens": 512},
                timeout=90
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            return f"ERROR: Faculty API also failed: {e}"

    return "ERROR: No VLM available"


# import os, base64, requests
# from pathlib import Path
# from dotenv import load_dotenv
# load_dotenv()

# FACULTY_API_BASE = os.getenv("FACULTY_API_BASE", "").rstrip("/")
# FACULTY_API_KEY  = os.getenv("FACULTY_API_KEY", "")
# VLM_MODEL = "hosted_vllm/llava-1.5-7b-hf"

# GROQ_API_KEY  = os.getenv("GROQ_API_KEY", "")
# GROQ_API_BASE = "https://api.groq.com/openai/v1"
# LLM_MODEL     = "llama-3.3-70b-versatile"


# def call_llm(prompt: str, system: str = None, temperature: float = 0.2) -> str:
#     messages = []
#     if system:
#         messages.append({"role": "system", "content": system})
#     messages.append({"role": "user", "content": prompt})
#     try:
#         r = requests.post(
#             f"{GROQ_API_BASE}/chat/completions",
#             headers={"Authorization": f"Bearer {GROQ_API_KEY}",
#                      "Content-Type": "application/json"},
#             json={"model": LLM_MODEL, "messages": messages,
#                   "temperature": temperature, "max_tokens": 1024},
#             timeout=30
#         )
#         r.raise_for_status()
#         return r.json()["choices"][0]["message"]["content"].strip()
#     except Exception as e:
#         return f"ERROR: {e}"


# def call_vlm(image_path: str, prompt: str) -> str:
#     p = Path(image_path)
#     if not p.exists():
#         return f"ERROR: Image not found at {image_path}"
#     with open(p, "rb") as f:
#         b64 = base64.b64encode(f.read()).decode()
#     media = "image/jpeg" if p.suffix.lower() in [".jpg",".jpeg"] else "image/png"
#     try:
#         r = requests.post(
#             f"{FACULTY_API_BASE}/chat/completions",
#             headers={"Authorization": f"Bearer {FACULTY_API_KEY}",
#                      "Content-Type": "application/json"},
#             json={"model": VLM_MODEL,
#                   "messages": [{"role": "user", "content": [
#                       {"type": "image_url", "image_url": {"url": f"data:{media};base64,{b64}"}},
#                       {"type": "text", "text": prompt}
#                   ]}],
#                   "temperature": 0.1, "max_tokens": 512},
#             timeout=90
#         )
#         r.raise_for_status()
#         return r.json()["choices"][0]["message"]["content"].strip()
#     except Exception as e:
#         return f"ERROR: {e}"