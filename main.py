# ============================================================
# main.py — Point d'entrée unique
# ============================================================

import os
from dotenv import load_dotenv
load_dotenv()

from waste_intelligence_agent.agents.master_agent_server import run_agent


def clean(p: str) -> str:
    p = p.strip().strip('"').strip("'").strip()
    return p if p else None


if __name__ == "__main__":
    print("\n" + "="*60)
    print("  WASTE INTELLIGENCE AGENT — Elmazraa Poultry Plant")
    print("="*60)
    print("  Send images and/or a question. Press Enter to skip.\n")

    w_rgb   = clean(input("  Water RGB image path:          "))
    w_micro = clean(input("  Water microscopic image path:  "))
    m_rgb   = clean(input("  Manure RGB image path:         "))
    m_micro = clean(input("  Manure microscopic image path: "))
    req     = input("  Your question or request:      ").strip() or None

    if not any([w_rgb, w_micro, m_rgb, m_micro, req]):
        print("\n  [!] No input provided. Please send at least one image or a question.")
    else:
        run_agent(
            water_rgb=w_rgb,
            water_micro=w_micro,
            manure_rgb=m_rgb,
            manure_micro=m_micro,
            user_request=req,
        )