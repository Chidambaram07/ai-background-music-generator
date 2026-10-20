import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.main import app


if __name__ == "__main__":
    contract = app.openapi()
    destination = ROOT / "openapi.yaml"
    destination.write_text(json.dumps(contract, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"Exported {len(contract['paths'])} API paths to {destination} (JSON, a valid YAML subset).")