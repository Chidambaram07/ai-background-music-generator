import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    run = ROOT / "test_results" / ("integration-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    run.mkdir(parents=True,exist_ok=False)
    environment = {**os.environ,"AUDIO_PROVIDER":"procedural","LLM_PROVIDER":"mock"}
    commands = [
        [sys.executable,"-m","pytest","-q","--tb=short","--basetemp="+str(run/"scratch"),"--junitxml="+str(run/"backend.xml")],
        ["node","node_modules/vitest/vitest.mjs","run","--reporter=junit","--outputFile="+str(run/"frontend.xml")],
        ["node","node_modules/vite/bin/vite.js","build"],
    ]
    for command in commands:
        result = subprocess.run(command,cwd=ROOT,env=environment)
        if result.returncode:
            raise SystemExit(result.returncode)
    print(f"All backend, integration/E2E, frontend and build gates passed. Evidence: {run}")