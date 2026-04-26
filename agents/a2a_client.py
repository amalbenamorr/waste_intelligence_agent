

import os, asyncio, aiohttp, time, base64, sys
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
load_dotenv()

WATER_AGENT_URL  = f"http://localhost:{os.getenv('WATER_AGENT_PORT', 8001)}"
MANURE_AGENT_URL = f"http://localhost:{os.getenv('MANURE_AGENT_PORT', 8002)}"

AGENT_TIMEOUT = int(os.getenv("AGENT_TIMEOUT", 120))


# ── Safe print for Windows terminals ──────────────────────────

def _safe_print(msg: str):
    """
    FIX v4: Print safely on Windows terminals with cp1252 encoding.
    Replaces unprintable characters instead of crashing.
    """
    try:
        print(msg)
    except (UnicodeEncodeError, UnicodeDecodeError):
        encoding = sys.stdout.encoding or 'utf-8'
        safe = str(msg).encode(encoding, errors='replace').decode(encoding, errors='replace')
        print(safe)


# ── Utility: strip None from payload ──────────────────────────

def _strip_none(d: dict) -> dict:
    """Remove all keys with None values from payload dict."""
    return {k: v for k, v in d.items() if v is not None}


# ── Path normalization ─────────────────────────────────────────

def _normalize_path_safe(p) -> Optional[str]:
    """
    FIX v4: Normalize path for cross-platform use.
    - Strips whitespace and quotes
    - Converts backslashes to forward slashes
    - Returns None if empty/invalid
    """
    if not p:
        return None
    if not isinstance(p, str):
        return None
    p = p.strip().strip('"').strip("'").strip()
    if not p or p.lower() in ("none", "null", ""):
        return None
    # Normalize Windows backslashes
    p = p.replace("\\", "/")
    return p


# ── Agent Registry ─────────────────────────────────────────────

class AgentRegistry:
    def __init__(self):
        self._cards: dict[str, dict] = {}
        self._available: dict[str, bool] = {}

    async def discover(self, agent_urls: list[str]) -> dict[str, dict]:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5)
        ) as session:
            tasks = [self._fetch_card(session, url) for url in agent_urls]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for url, result in zip(agent_urls, results):
            if isinstance(result, dict) and "id" in result:
                agent_id = result["id"]
                self._cards[agent_id] = {**result, "_base_url": url}
                self._available[agent_id] = True
                _safe_print(f"  [Registry] Discovered: {result['name']} @ {url}")
            else:
                _safe_print(f"  [Registry] Agent unavailable: {url}")

        return self._cards

    async def _fetch_card(self, session: aiohttp.ClientSession, base_url: str) -> dict:
        card_url = f"{base_url}/.well-known/agent.json"
        async with session.get(card_url) as resp:
            if resp.status == 200:
                return await resp.json()
            raise Exception(f"HTTP {resp.status}")

    def get_agents_with_capability(self, capability: str) -> list[dict]:
        return [
            card for card in self._cards.values()
            if capability in card.get("capabilities", [])
            and self._available.get(card["id"], False)
        ]

    def get_agent_by_id(self, agent_id: str) -> Optional[dict]:
        return self._cards.get(agent_id)

    @property
    def available_agents(self) -> list[str]:
        return [aid for aid, avail in self._available.items() if avail]


# ── Image encoding ─────────────────────────────────────────────

def _encode_image_b64(path: str) -> Optional[str]:
    """
    FIX v4: Encode image to base64.
    - Normalizes path before use (handles Windows backslashes)
    - Opens file in binary mode ('rb') — no charmap issue
    - Decodes b64 as ASCII (b64 alphabet is always ASCII-safe)
    - Uses _safe_print for all console output
    """
    if not path:
        return None
    try:
        # Normalize path
        normalized = _normalize_path_safe(path)
        if not normalized:
            return None

        p = Path(normalized)
        if not p.exists() or not p.is_file():
            _safe_print(f"  [A2A] Image not found at path (check spelling/permissions)")
            return None

        # Binary read — no encoding involved, no charmap error possible
        with open(p, "rb") as f:
            data = f.read()

        if len(data) == 0:
            _safe_print(f"  [A2A] Image file is empty")
            return None

        # b64 alphabet is pure ASCII — safe on all platforms
        encoded = base64.b64encode(data).decode("ascii")
        _safe_print(f"  [A2A] Image encoded: {len(data)} bytes -> {len(encoded)} b64 chars OK")
        return encoded

    except Exception as e:
        # Encode error message safely for Windows terminal
        err_safe = str(e).encode('ascii', errors='replace').decode('ascii')
        _safe_print(f"  [A2A] Image encoding error: {err_safe}")
        return None


def _is_valid_path(p) -> bool:
    """
    FIX v4: Check if path is valid and file exists.
    Normalizes path before checking (handles backslashes).
    """
    if p is None:
        return False
    if not isinstance(p, str):
        return False
    normalized = _normalize_path_safe(p)
    if not normalized:
        return False
    try:
        return Path(normalized).exists() and Path(normalized).is_file()
    except Exception:
        return False


def _build_image_payload(rgb_path: Optional[str], micro_path: Optional[str],
                          prefix: str) -> dict:
    """
    FIX v4: Build image payload using ONLY base64 encoding.
    - Never sends file paths to remote agents (avoids path/OS issues)
    - Only includes keys with actual b64 data (no None, no empty string)
    """
    payload = {}

    if rgb_path:
        b64 = _encode_image_b64(rgb_path)
        if b64:
            payload[f"{prefix}_rgb_b64"] = b64

    if micro_path:
        b64 = _encode_image_b64(micro_path)
        if b64:
            payload[f"{prefix}_micro_b64"] = b64

    return {k: v for k, v in payload.items() if v}


# ── Async delegation ───────────────────────────────────────────

async def _call_agent(
    session: aiohttp.ClientSession,
    agent_card: dict,
    payload: dict,
    session_id: str,
) -> dict:
    endpoint = agent_card.get("endpoint") or (agent_card["_base_url"] + "/analyze")
    try:
        clean_payload = _strip_none({**payload, "session_id": session_id})
        _safe_print(f"  [A2A] POST {endpoint} | keys: {list(clean_payload.keys())}")

        async with session.post(
            endpoint,
            json=clean_payload,
            timeout=aiohttp.ClientTimeout(total=AGENT_TIMEOUT),
        ) as resp:
            if resp.status == 200:
                result = await resp.json()
                status = result.get('status', '?')
                error  = result.get('error', 'none')
                _safe_print(f"  [A2A] {agent_card['name']} -> status={status} error={error}")
                return result
            text = await resp.text()
            err_safe = text[:300].encode('ascii', errors='replace').decode('ascii')
            _safe_print(f"  [A2A] HTTP {resp.status} from {agent_card['name']}: {err_safe}")
            return {
                "status": "error",
                "error": f"HTTP {resp.status}: {text[:200]}",
                "agent_id": agent_card.get("id", "unknown"),
            }
    except asyncio.TimeoutError:
        _safe_print(f"  [A2A] Timeout from {agent_card['name']}")
        return {
            "status": "error",
            "error": f"Timeout after {AGENT_TIMEOUT}s",
            "agent_id": agent_card.get("id", "unknown"),
        }
    except Exception as e:
        err_safe = str(e).encode('ascii', errors='replace').decode('ascii')
        _safe_print(f"  [A2A] Exception calling {agent_card['name']}: {err_safe}")
        return {
            "status": "error",
            "error": err_safe,
            "agent_id": agent_card.get("id", "unknown"),
        }


async def delegate_analyses_parallel(
    registry: AgentRegistry,
    water_rgb_path:    Optional[str],
    water_micro_path:  Optional[str],
    manure_rgb_path:   Optional[str],
    manure_micro_path: Optional[str],
    user_request:      Optional[str],
    session_id:        str,
    status_callback=None,
) -> dict:
    """
    FIX v4: Dynamic parallel delegation.
    - Normalizes ALL paths before any processing
    - Encodes images to b64 only (no path transmission to agents)
    - Safe prints (no charmap crash on Windows)
    - Never sends None in JSON payload
    """
    tasks = []
    task_labels = []

    # Normalize all paths first
    water_rgb_path    = _normalize_path_safe(water_rgb_path)
    water_micro_path  = _normalize_path_safe(water_micro_path)
    manure_rgb_path   = _normalize_path_safe(manure_rgb_path)
    manure_micro_path = _normalize_path_safe(manure_micro_path)

    has_water  = _is_valid_path(water_rgb_path)  or _is_valid_path(water_micro_path)
    has_manure = _is_valid_path(manure_rgb_path) or _is_valid_path(manure_micro_path)

    _safe_print(f"  [A2A] has_water={has_water} has_manure={has_manure}")

    if not has_water and not has_manure:
        return {
            "water_result": None,
            "manure_result": None,
            "delegation_errors": [
                "No valid image paths — check that files exist at the given paths"
            ],
            "agents_called": [],
            "parallel_time_s": 0.0,
        }

    # user_request always as non-None string
    safe_request = user_request if isinstance(user_request, str) else ""

    async with aiohttp.ClientSession() as session:

        if has_water:
            agents = registry.get_agents_with_capability("wastewater_rgb_analysis")
            if not agents:
                agents = [registry.get_agent_by_id("water-analysis-agent")]
                agents = [a for a in agents if a]

            if agents:
                agent = agents[0]
                payload = _build_image_payload(water_rgb_path, water_micro_path, "water")

                if not payload:
                    _safe_print(f"  [A2A] Water image encoding failed — skipping water agent")
                else:
                    payload["user_request"] = safe_request
                    payload = _strip_none(payload)

                    if status_callback:
                        status_callback(agent["name"], "running")
                    tasks.append(_call_agent(session, agent, payload, session_id))
                    task_labels.append(("water", agent))
                    _safe_print(f"  [A2A] Water agent queued | b64 keys: {[k for k in payload if 'b64' in k]}")

        if has_manure:
            agents = registry.get_agents_with_capability("manure_rgb_analysis")
            if not agents:
                agents = [registry.get_agent_by_id("manure-analysis-agent")]
                agents = [a for a in agents if a]

            if agents:
                agent = agents[0]
                payload = _build_image_payload(manure_rgb_path, manure_micro_path, "manure")

                if not payload:
                    _safe_print(f"  [A2A] Manure image encoding failed — skipping manure agent")
                else:
                    payload["user_request"] = safe_request
                    payload = _strip_none(payload)

                    if status_callback:
                        status_callback(agent["name"], "running")
                    tasks.append(_call_agent(session, agent, payload, session_id))
                    task_labels.append(("manure", agent))
                    _safe_print(f"  [A2A] Manure agent queued | b64 keys: {[k for k in payload if 'b64' in k]}")

        if not tasks:
            return {
                "water_result": None,
                "manure_result": None,
                "delegation_errors": ["Image encoding failed for all provided paths"],
                "agents_called": [],
                "parallel_time_s": 0.0,
            }

        t0 = time.time()
        _safe_print(f"  [A2A] Launching {len(tasks)} agent(s) in parallel...")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        parallel_time = round(time.time() - t0, 2)
        _safe_print(f"  [A2A] All done in {parallel_time}s")

    output = {
        "water_result":      None,
        "manure_result":     None,
        "delegation_errors": [],
        "agents_called":     [],
        "parallel_time_s":   parallel_time,
    }

    for (label, agent_card), result in zip(task_labels, results):
        agent_name = agent_card["name"]

        if isinstance(result, Exception):
            err = str(result).encode('ascii', errors='replace').decode('ascii')
            output["delegation_errors"].append(f"{agent_name}: {err}")
            if status_callback:
                status_callback(agent_name, "error")
            continue

        output["agents_called"].append({
            "agent_id":   agent_card["id"],
            "agent_name": agent_name,
            "status":     result.get("status", "unknown"),
            "time_s":     result.get("processing_time_s", 0),
        })

        if result.get("status") == "success":
            if status_callback:
                status_callback(agent_name, "done")
        else:
            if status_callback:
                status_callback(agent_name, "error")

        if label == "water":
            output["water_result"] = result
        elif label == "manure":
            output["manure_result"] = result

    return output


async def check_agents_health(agent_urls: list[str]) -> dict[str, bool]:
    """Check health of each agent."""
    results = {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
        for url in agent_urls:
            try:
                async with session.get(f"{url}/health") as resp:
                    results[url] = resp.status == 200
            except Exception:
                results[url] = False
    return results