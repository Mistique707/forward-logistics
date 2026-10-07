"""One command for the demo: set up, seed, train, build the dashboard and serve it.

    py -3.13 demo.py                 # then open http://localhost:8000 (opens automatically)
    py -3.13 demo.py --no-browser --port 8080
    py -3.13 demo.py --no-sensors     # without the simulated IoT sensor heartbeats

Re-running is safe and deterministic: the synthetic history and the models are rebuilt from
the fixed seed every time, so the demo scenario is identical on every run.
"""
import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
UI = ROOT / "frontend"


def sh(*cmd, cwd=ROOT):
    print("  $", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, cwd=cwd)


def bootstrap():
    """Create the venv and install requirements, then re-run this script inside it."""
    if not PY.exists():
        if sys.version_info[:2] != (3, 13):
            sys.exit(f"Python 3.13 is required (LightGBM / OR-Tools wheels); run:  py -3.13 {Path(__file__).name}")
        print("[1/5] creating virtual environment", flush=True)
        venv.create(VENV, with_pip=True)
    print("[1/5] installing Python requirements", flush=True)
    sh(PY, "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", "requirements.txt")
    sys.exit(subprocess.run([str(PY), __file__, *sys.argv[1:]], cwd=ROOT).returncode)


def build_ui():
    dist = UI / "dist" / "index.html"
    sources = [p for p in (UI / "src").rglob("*")] + list((UI / "public").rglob("*")) + [UI / "index.html"]
    if dist.exists() and dist.stat().st_mtime > max(p.stat().st_mtime for p in sources):
        print("[4/5] dashboard build is up to date", flush=True)
        return
    npm = shutil.which("npm")
    if not npm:
        sys.exit("Node.js (npm) is required to build the dashboard: https://nodejs.org")
    print("[4/5] building the dashboard", flush=True)
    if not (UI / "node_modules").exists():
        sh(npm, "ci", "--no-audit", "--no-fund", cwd=UI)
    sh(npm, "run", "build", cwd=UI)


def announce_when_ready(url, browser):
    for _ in range(240):
        try:
            urllib.request.urlopen(url + "/api/scenarios", timeout=1)
        except OSError:
            time.sleep(0.5)
            continue
        print(f"\n  Dashboard ready:   {url}\n  Inventory check:   {url}/field/\n", flush=True)
        if browser:
            webbrowser.open(url)
        return


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-sensors", action="store_true", help="do not run the simulated IoT sensors")
    args = ap.parse_args()
    if Path(sys.prefix).resolve() != VENV.resolve():
        bootstrap()

    sys.path.insert(0, str(ROOT))
    from backend.config import DATA
    if not (DATA / "network.json").exists() or not (DATA / "weather.csv").exists():
        print("[2/5] fetching open data (OSM, Open-Meteo)", flush=True)
        sh(PY, "-m", "backend.fetch_data")
    print("[2/5] building the history (real weather, real Zoji La closures)", flush=True)
    from backend.simulator import simulate
    simulate()
    print("[3/5] training and validating the forecast models", flush=True)
    from backend.forecast import train_all
    train_all()
    build_ui()

    url = f"http://localhost:{args.port}"
    print(f"[5/5] serving on {url}  (precomputing scenarios, Ctrl+C to stop)", flush=True)
    threading.Thread(target=announce_when_ready, args=(url, not args.no_browser), daemon=True).start()
    if not args.no_sensors:  # tank-level sensors and load cells reporting every 20 s (heartbeats unless stock moves)
        from backend import iot_sim
        threading.Thread(target=iot_sim.run, args=(url,), daemon=True).start()
    import uvicorn
    uvicorn.run("backend.api:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
