# ============================================================
# utils/test_all.py — Tests complets avant lancement
# Lance ce fichier AVANT main.py
# ============================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()


def test_llm():
    print("\n[1] LLM (LLaMA 3.1 70B)...")
    from utils.models import call_llm
    r = call_llm("Reply: LLM_OK", temperature=0.0)
    ok = "ERROR" not in r
    print(f"  {'✅' if ok else '❌'} {r[:50]}")
    return ok

def test_vlm():
    print("\n[2] VLM (LLaVA 1.5 7B)...")
    try:
        from PIL import Image
        import tempfile
        img = Image.new("RGB", (50,50), color=(100,150,200))
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        tmp.close()
        img.save(tmp.name)
        from utils.models import call_vlm
        r = call_vlm(tmp.name, "What color? 3 words.")
        os.unlink(tmp.name)
        ok = "ERROR" not in r
        print(f"  {'✅' if ok else '❌'} {r[:50]}")
        return ok
    except Exception as e:
        print(f"  ❌ {e}")
        return False

def test_tavily():
    print("\n[3] Tavily Web Search...")
    try:
        from tavily import TavilyClient
        key = os.getenv("TAVILY_API_KEY","")
        if not key or "your" in key:
            print("  ⚠ Key not set — skip")
            return True
        r = TavilyClient(api_key=key).search("poultry waste Tunisia", max_results=1)
        ok = len(r.get("results",[])) > 0
        print(f"  {'✅' if ok else '❌'} Found {len(r.get('results',[]))} results")
        return ok
    except Exception as e:
        print(f"  ❌ {e}")
        return False

def test_chromadb():
    print("\n[4] ChromaDB...")
    try:
        import chromadb
        from pathlib import Path
        path = os.getenv("CHROMA_DB_PATH","./outputs/chromadb")
        Path(path).mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=path)
        col = client.get_or_create_collection("test")
        col.add(documents=["test"], ids=["t1"])
        r = col.query(query_texts=["test"], n_results=1)
        client.delete_collection("test")
        print(f"  ✅ ChromaDB works")
        return True
    except Exception as e:
        print(f"  ❌ {e}")
        return False

def test_tools():
    print("\n[5] Tools import test...")
    try:
        from tools.tool_water import analyze_water
        from tools.tool_manure import analyze_manure
        from tools.tool_adaptive import adaptive_intelligence
        from tools.tool_risk import search_risk
        from tools.tool_valorization import search_valorization
        from tools.tool_roi import calculate_roi
        from tools.tool_memory import save_to_memory
        from tools.tool_report import generate_report
        print("  ✅ All 8 tools imported successfully")
        return True
    except Exception as e:
        print(f"  ❌ Import failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_roi_tool():
    print("\n[6] ROI Calculator (no API needed)...")
    try:
        from tools.tool_roi import calculate_roi
        result = calculate_roi.invoke({
            "water_liters": 500.0,
            "manure_kg": 100.0,
            "product_type": "biostimulant",
            "price_per_liter": 2.5
        })
        ok = "TND" in result
        print(f"  {'✅' if ok else '❌'} {result[:80]}")
        return ok
    except Exception as e:
        print(f"  ❌ {e}")
        return False


if __name__ == "__main__":
    print("="*60)
    print("WASTE INTELLIGENCE AGENT v2 — Tests")
    print("="*60)

    results = {
        "LLM":      test_llm(),
        "VLM":      test_vlm(),
        "Tavily":   test_tavily(),
        "ChromaDB": test_chromadb(),
        "Tools":    test_tools(),
        "ROI Tool": test_roi_tool(),
    }

    print("\n" + "="*60)
    all_ok = True
    for name, ok in results.items():
        print(f"  {name:12s} {'✅ OK' if ok else '❌ FAILED'}")
        if not ok:
            all_ok = False
    print("="*60)
    print("✅ Ready to run main.py" if all_ok else "⚠ Fix errors first")