

import subprocess, sys, os, time, atexit
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

BASE_DIR = Path(__file__).parent.resolve()

WATER_PORT  = int(os.getenv("WATER_AGENT_PORT",  8001))
MANURE_PORT = int(os.getenv("MANURE_AGENT_PORT", 8002))
MASTER_PORT = int(os.getenv("MASTER_PORT",        8000))

procs = []


def cleanup():
    print("\n[StartAll] Stopping all processes...")
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    time.sleep(1)
    for p in procs:
        try:
            p.kill()
        except Exception:
            pass
    print("[StartAll] All stopped.")


atexit.register(cleanup)


def wait_for_port(port: int, timeout: int = 30) -> bool:
    import socket
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except Exception:
            time.sleep(0.5)
    return False


def launch(script_path: str, label: str) -> subprocess.Popen:
    print(f"[StartAll] Launching {label}...")

    # FIX: inject PYTHONIOENCODING=utf-8 so subprocesses don't crash
    # with 'charmap' codec errors on Windows terminals (cp1252)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"  # Python 3.7+ universal newlines mode

    p = subprocess.Popen(
        [sys.executable, str(script_path)],
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",      # FIX: read subprocess output as UTF-8
        errors="replace",      # FIX: replace unreadable chars instead of crashing
        bufsize=1,
        env=env,               # FIX: pass modified environment
    )

    import threading
    def _forward():
        try:
            for line in p.stdout:
                try:
                    print(f"  [{label}] {line}", end="")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    # Safe fallback for Windows terminal
                    safe_line = line.encode('ascii', errors='replace').decode('ascii')
                    print(f"  [{label}] {safe_line}", end="")
        except Exception:
            pass

    t = threading.Thread(target=_forward, daemon=True)
    t.start()

    return p


if __name__ == "__main__":
    # Also set encoding for this process
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    print("="*60)
    print("  Elmazraa Waste Intelligence — A2A Full Stack")
    print("="*60)
    print(f"  water_agent  -> http://localhost:{WATER_PORT}")
    print(f"  manure_agent -> http://localhost:{MANURE_PORT}")
    print(f"  master+dashboard -> http://localhost:{MASTER_PORT}")
    print("="*60)
    print()

    # 1. Launch water agent
    p_water = launch(BASE_DIR / "agents" / "water_agent_server.py", "WaterAgent")
    procs.append(p_water)
    print(f"  Waiting for water_agent on port {WATER_PORT}...")
    if wait_for_port(WATER_PORT, 20):
        print(f"  [OK] water_agent ready on port {WATER_PORT}")
    else:
        print(f"  [WARN] water_agent did not start in time — continuing anyway")

    # 2. Launch manure agent
    p_manure = launch(BASE_DIR / "agents" / "manure_agent_server.py", "ManureAgent")
    procs.append(p_manure)
    print(f"  Waiting for manure_agent on port {MANURE_PORT}...")
    if wait_for_port(MANURE_PORT, 20):
        print(f"  [OK] manure_agent ready on port {MANURE_PORT}")
    else:
        print(f"  [WARN] manure_agent did not start in time — continuing anyway")

    # 3. Launch master + dashboard
    p_master = launch(BASE_DIR / "main_a2a.py", "Master")
    procs.append(p_master)
    print(f"  Waiting for master on port {MASTER_PORT}...")
    if wait_for_port(MASTER_PORT, 20):
        print(f"\n  [OK] All services ready!")
        print(f"  Dashboard: http://localhost:{MASTER_PORT}")
        print(f"  Press Ctrl+C to stop all.\n")
    else:
        print(f"  [WARN] Master did not start in time")

    # Keep alive
    try:
        while True:
            for p, label in [(p_water, "WaterAgent"), (p_manure, "ManureAgent"), (p_master, "Master")]:
                if p.poll() is not None:
                    print(f"  [WARN] {label} exited with code {p.returncode}")
            time.sleep(5)
    except KeyboardInterrupt:
        pass