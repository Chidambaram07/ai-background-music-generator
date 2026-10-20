from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from backend.tests.http_client import IdempotentTestClient as TestClient

from app.core.config import get_settings
from app.services.audio_processing import AudioProcessor
from app.services.manifest import file_hash
from app.services.music_generation import MusicGenerationEngine


def wait_job(client, job_id, timeout=240):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        response.raise_for_status()
        job = response.json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        worker = client.app.state.job_worker
        if worker._task.done():
            raise RuntimeError(f"Worker stopped before job completion: {worker._task.exception()}")
        time.sleep(0.05)
    raise TimeoutError(f"Job {job_id} did not finish within {timeout}s; last status={job['status']}, progress={job['progress']}; worker={worker._task}")


def generate(client, request, key=None):
    response = client.post("/api/v1/music/jobs", json=request, headers={"Idempotency-Key": key or uuid4().hex})
    assert response.status_code == 202, response.text
    accepted = response.json()
    assert accepted["job_id"] and accepted["status"] in {"queued", "processing", "succeeded"}
    return wait_job(client, accepted["job_id"])


def measure_job(directory, requested):
    directory = Path(directory)
    manifest = json.loads((directory / "music_manifest.json").read_text(encoding="utf-8"))
    mix, rate = sf.read(directory / "mix.wav", dtype="float32")
    processor = AudioProcessor()
    summed = np.zeros_like(mix, dtype=np.float64)
    stem_checks = []
    hashes = []
    for stem in manifest["stems"]:
        path = (directory / stem["file"]).resolve()
        assert path.is_relative_to(directory.resolve()), "Manifest path escapes job"
        audio, stem_rate = sf.read(path, dtype="float32")
        aligned = stem_rate == rate and audio.shape == mix.shape
        assert aligned, f"Stem alignment failed: {stem['name']}"
        summed += audio * 10 ** (stem["gain_db"] / 20)
        hashes.append(file_hash(path))
        stem_checks.append({"name": stem["name"], "duration_s": len(audio) / stem_rate, "sample_rate": stem_rate, "aligned": aligned})
    summed *= 10 ** (manifest["mix"]["master_gain_db"] / 20)
    residual = np.sqrt(np.mean((summed - mix) ** 2))
    reference = np.sqrt(np.mean(mix.astype(np.float64) ** 2))
    reconstruction = float(20 * np.log10(max(float(residual), 1e-15) / max(float(reference), 1e-15)))
    duration = len(mix) / rate
    loudness = float(pyln.Meter(rate).integrated_loudness(mix))
    peak = processor._true_peak_dbtp(mix)
    checks = {
        "duration": abs(duration - requested["duration_s"]) <= 0.05,
        "sample_rate": rate == 48000,
        "loudness": abs(loudness - requested.get("output", {}).get("target_lufs", -16)) <= 1,
        "true_peak": peak <= -1,
        "no_clipping": not processor._detect_clipping(mix),
        "stem_reconstruction": reconstruction <= -40,
        "independent_files": len(hashes) >= 3 and len(set(hashes)) == len(hashes),
        "intentional_boundary_fade": manifest["validation"]["checks"]["fades"]["pass"] is True,
        "manifest_validation": manifest["validation"]["valid"] is True,
    }
    return {"actual_duration_s": duration, "duration_error_s": abs(duration - requested["duration_s"]),
            "sample_rate": rate, "lufs": loudness, "true_peak_dbtp": peak,
            "stem_reconstruction_error_db": reconstruction, "validation": checks,
            "stem_checks": stem_checks, "warnings": manifest["warnings"],
            "generation_time_s": manifest["generation"]["generation_time_s"], "cost": manifest["generation"]["cost"],
            "output_paths": [str(path) for path in sorted(directory.rglob("*")) if path.is_file()]}


def regeneration_check(client, job_id, directory):
    directory = Path(directory)
    before = json.loads((directory / "music_manifest.json").read_text())
    hashes_before = {item["name"]: file_hash(directory / item["file"]) for item in before["stems"]}
    target = "drums"
    assert target in hashes_before, "This case requires a drums stem"
    mix_before = file_hash(directory / "mix.wav")
    response = client.post(f"/api/v1/music/jobs/{job_id}/stems/{target}/regenerate", json={"instruction": "More energetic drums with syncopated hats"})
    assert response.status_code == 202, response.text
    completed = wait_job(client, response.json()["job_id"])
    assert completed["status"] == "succeeded", completed
    after = json.loads((directory / "music_manifest.json").read_text())
    hashes_after = {item["name"]: file_hash(directory / item["file"]) for item in after["stems"]}
    old_metadata = {item["name"]: item for item in before["stems"]}
    for item in after["stems"]:
        name = item["name"]
        if name == target:
            assert hashes_after[name] != hashes_before[name]
            assert item["generation_job_id"] != old_metadata[name]["generation_job_id"]
        else:
            assert hashes_after[name] == hashes_before[name]
            assert item == old_metadata[name], f"Unchanged metadata changed: {name}"
    assert file_hash(directory / "mix.wav") != mix_before
    assert after["generation"]["updated_at_ns"] > before["generation"]["updated_at_ns"]
    measured = measure_job(directory, after["request"])
    assert all(measured["validation"].values()), measured
    return {"hashes_before": hashes_before, "hashes_after": hashes_after,
            "mix_before": mix_before, "mix_after": file_hash(directory / "mix.wav"),
            "regeneration_job_id": completed["job_id"], "measurements": measured}


def narration_fixture(kind, duration=6, rate=48000):
    audio = np.zeros(round(duration * rate), dtype=np.float32)
    if kind != "no-speech":
        start, end = (0.5, 2.0) if kind == "normal" else (0.5, 0.9)
        times = np.arange(round((end - start) * rate)) / rate
        audio[round(start * rate):round(end * rate)] = 0.12 * np.sin(2 * np.pi * 180 * times)
    buffer = io.BytesIO()
    sf.write(buffer, audio, rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def main():
    parser = argparse.ArgumentParser(description="Real, isolated IncuBrix assessment evidence; exits nonzero on failure.")
    parser.add_argument("--results-dir", type=Path, default=ROOT / "test_results")
    parser.add_argument("--quick", action="store_true", help="Only 5s, 6s and regeneration; not full assessment coverage")
    parser.add_argument("--report", type=Path, help="Also publish this run's measured Markdown report at this path")
    args = parser.parse_args()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    run = args.results_dir.resolve() / run_id
    for name in ("logs", "outputs", "manifests"):
        (run / name).mkdir(parents=True, exist_ok=False)
    os.environ.update(DATABASE_PATH=str(run / "jobs.db"), AUDIO_OUTPUT_DIR=str(run / "outputs"), LLM_PROVIDER="mock", LLM_API_KEY="", HF_API_KEY="", OLLAMA_ENABLED="false")
    get_settings.cache_clear()
    from app.main import app
    handler = logging.FileHandler(run / "logs" / "application.log", encoding="utf-8")
    logging.getLogger().addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    records = []

    def record(test_id, request, action):
        row = {"test_id": test_id, "request": request, "job_id": None, "status": "FAIL",
               "requested_duration_s": request.get("duration_s"), "actual_duration_s": None,
               "duration_error_s": None, "sample_rate": None, "lufs": None, "true_peak_dbtp": None,
               "stem_reconstruction_error_db": None, "validation": {}, "output_paths": [],
               "warnings": [], "errors": [], "generation_time_s": None, "cost": None}
        try:
            row.update(action())
            row["status"] = "PASS"
        except Exception as exc:
            if exc.args and isinstance(exc.args[0], dict) and exc.args[0].get("job_id"):
                failed_job = exc.args[0]
                row["job_id"] = failed_job["job_id"]
                row["job_error"] = failed_job.get("error")
                row["output_paths"] = [str(path) for path in (run / "outputs" / failed_job["job_id"]).rglob("*") if path.is_file()]
            row["errors"].append(f"{type(exc).__name__}: {exc}")
            logging.exception("Assessment case failed: %s", test_id)
        records.append(row)
        (run / "logs" / f"{test_id}.json").write_text(json.dumps(row, indent=2, allow_nan=False), encoding="utf-8")
        print(f"{test_id}: {row['status']}", flush=True)

    with TestClient(app) as client:
        durations = [5, 6, 45] if args.quick else [5, 6, 10, 30, 45, 60, 180, 300]
        cases = [{"test_id": f"duration-{duration}", "request": {"prompt": "Cinematic hopeful instrumental background", "duration_s": duration, "output": {"formats": ["wav", "mp3"]}}} for duration in durations]
        if not args.quick:
            cases.extend(json.loads((ROOT / "tests/fixtures/assessment_requests.json").read_text()))
        jobs = {}
        for case in cases:
            request = case["request"]
            def run_case(request=request, test_id=case["test_id"]):
                job = generate(client, request)
                jobs[test_id] = job
                assert job["status"] == "succeeded", job
                directory = run / "outputs" / job["job_id"]
                metrics = measure_job(directory, request)
                assert all(metrics["validation"].values()), metrics
                shutil.copy2(directory / "music_manifest.json", run / "manifests" / f"{test_id}.json")
                return {"job_id": job["job_id"], **metrics}
            record(case["test_id"], request, run_case)
        if "duration-45" in jobs:
            job = jobs["duration-45"]
            record("regeneration-drums", {"duration_s":45}, lambda: {"job_id":job["job_id"], **regeneration_check(client, job["job_id"], run / "outputs" / job["job_id"])})
        if not args.quick:
            invalid_sections = [
                [{"start":-1,"end":6,"mood":"calm"}],
                [{"start":7,"end":8,"mood":"calm"}],
                [{"start":0,"end":4,"mood":"calm"},{"start":3,"end":6,"mood":"hopeful"}],
                [{"start":0,"end":1,"mood":"calm"},{"start":1,"end":6,"mood":"hopeful"}],
                [{"start":"malformed","end":6,"mood":"calm"}],
            ]
            for index, sections in enumerate(invalid_sections, start=1):
                request = {"prompt":"Invalid section case", "duration_s":6, "sections":sections}
                def invalid_section(request=request):
                    response = client.post("/api/v1/music/jobs", json=request)
                    assert response.status_code == 422 and response.json()["error"]["code"], response.text
                    return {"validation":{"invalid_sections_rejected":True}, "response":response.json()}
                record(f"invalid-sections-{index}", request, invalid_section)
            request = {"prompt":"Calm then hopeful cinematic background", "duration_s":10,
                       "tempo_bpm":120, "loopable":True,
                       "sections":[{"start":0,"end":5,"mood":"calm"},{"start":5,"end":10,"mood":"hopeful"}]}
            def loop_case():
                job = generate(client, request)
                assert job["status"] == "succeeded", job
                assert job["result"]["validation_result"]["checks"]["looping"]["pass"]
                metrics = measure_job(run / "outputs" / job["job_id"], request)
                assert all(metrics["validation"].values()), metrics
                return {"job_id":job["job_id"], **metrics, "loop_validation":job["result"]["validation_result"]["checks"]["looping"]}
            record("sections-and-loop", request, loop_case)
            for duration in (0, -1, 301):
                request = {"prompt":"Invalid duration", "duration_s":duration}
                def invalid(request=request):
                    response = client.post("/api/v1/music/jobs", json=request)
                    assert response.status_code == 422, response.text
                    assert response.json()["error"]["code"]
                    return {"validation":{"rejected":True}, "errors":[], "response":response.json()}
                record(f"invalid-duration-{duration}", request, invalid)
            for kind in ("normal", "long-silence", "no-speech"):
                def narration(kind=kind):
                    uploaded = client.post("/api/v1/music/upload", files={"file":(f"{kind}.wav", narration_fixture(kind), "audio/wav")})
                    assert uploaded.status_code == 200, uploaded.text
                    payload = {"prompt":"Calm narration background", "duration_s":6, "narration_url":uploaded.json()["url"]}
                    job = generate(client, payload)
                    if kind == "no-speech":
                        assert job["status"] == "succeeded" and job["result"]["validation_result"]["checks"]["ducking"]["no_speech_unchanged"], job
                        return {"job_id":job["job_id"], "validation":{"no_speech_music_unchanged":True}, "warnings":["No speech detected; music remains unchanged, without mixed narration."], "response":job}
                    assert job["status"] == "succeeded", job
                    metrics = measure_job(run / "outputs" / job["job_id"], payload)
                    assert all(metrics["validation"].values()), metrics
                    return {"job_id":job["job_id"], **metrics}
                record(f"narration-{kind}", {"duration_s":6}, narration)
            def duplicate():
                payload = {"prompt":"Idempotent request", "duration_s":5}
                key = uuid4().hex
                first = generate(client, payload, key)
                directory = run / "outputs" / first["job_id"]
                before = file_hash(directory / "mix.wav")
                second = generate(client, payload, key)
                assert first["job_id"] == second["job_id"]
                assert file_hash(directory / "mix.wav") == before
                return {"job_id":first["job_id"], "validation":{"same_job_and_artifact":True}}
            record("idempotency", {"duration_s":5}, duplicate)
            def concurrent():
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(generate, client, {"prompt":prompt, "duration_s":5, "seed":seed}) for seed, prompt in ((11,"Calm piano"),(12,"Energetic electronic"))]
                    completed = [future.result() for future in futures]
                assert all(job["status"] == "succeeded" for job in completed), completed
                assert completed[0]["job_id"] != completed[1]["job_id"]
                metrics = [measure_job(run / "outputs" / job["job_id"], {"duration_s":5}) for job in completed]
                assert all(all(item["validation"].values()) for item in metrics)
                return {"job_ids":[job["job_id"] for job in completed], "validation":{"separate_valid_outputs":True}, "measurements":metrics}
            record("concurrent-jobs", {"duration_s":5}, concurrent)
            def failure():
                with patch.object(MusicGenerationEngine, "generate", side_effect=TimeoutError("Injected provider timeout for failure-path testing")):
                    job = generate(client, {"prompt":"Provider failure test", "duration_s":5})
                assert job["status"] == "failed" and job["error"] and job["result"] is None, job
                return {"job_id":job["job_id"], "validation":{"structured_failure":True}, "response":job, "warnings":["Deliberate failure injection, not a paid-provider availability test."]}
            record("provider-failure", {"duration_s":5}, failure)
    summary = {"run_id":run_id, "mode":"quick" if args.quick else "full", "passed":sum(row["status"] == "PASS" for row in records), "failed":sum(row["status"] == "FAIL" for row in records), "skipped":0, "scope":"Functional/audio checks only; not proof of AI-audio generation, subjective music quality, copyright clearance, or perfect speech detection.", "tests":records}
    (run / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    with (run / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        for row in records:
            writer.writerow({key:json.dumps(row.get(key)) if isinstance(row.get(key), (dict,list)) else row.get(key) for key in writer.fieldnames})
    report_lines = [
        "# IncuBrix Assessment Evidence Report", "",
        f"Run: `{run_id}`. Mode: `{summary['mode']}`.", "",
        f"Executed {len(records)} batch checks: {summary['passed']} PASS, {summary['failed']} FAIL, {summary['skipped']} skipped.",
        "PASS means the assertions recorded for that case passed. It does not imply overall assessment compliance.", "",
        f"Evidence directory: `{run}`. Previous run directories are not overwritten.", "",
        "## Measured Duration Cases", "",
        "| Test | Requested s | Actual s | Rate Hz | LUFS | True Peak dBTP | Reconstruction dB | Status |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in records:
        if row["test_id"].startswith("duration-"):
            report_lines.append("| " + " | ".join(str(row.get(key)) for key in ("test_id", "requested_duration_s", "actual_duration_s", "sample_rate", "lufs", "true_peak_dbtp", "stem_reconstruction_error_db", "status")) + " |")
    report_lines.extend([
        "", "## Regeneration", "",
        "The regeneration-drums case stores actual before/after SHA-256 hashes, checks all unchanged stem metadata, requires a new mix hash, and remeasures the final WAV and reconstruction.",
        "See `logs/regeneration-drums.json`. Canonical stems already include the boundary mastering fades. Reconstruction uses each stem's gain_db plus mix.master_gain_db.",
        "", "## Scope And Limitations", "",
        "- Audio comes from the existing deterministic procedural synthesizer, not an AI audio model. ai_generated is false; provider is local; model and cost are null.",
        "- Ten prompt cases prove generation and technical output validity, not subjective prompt matching or musical quality.",
        "- Song/artist prompts are not reproduced using recordings. This is not a perceptual similarity or copyright-clearance test.",
        "- No external samples are used by the local renderer; commercial legal clearance remains unknown, not PASS.",
        "- Narration fixtures are synthetic speech-like waveforms. Energy detection is not robust speech recognition. Silent narration must leave music unchanged; ducked output contains music only with a manifest gain curve.",
        "- Loop evidence measures bar alignment and boundary discontinuity, not subjective seamlessness.",
        "- Two requests may be concurrent, but the existing worker renders its queue serially.",
        "- Provider failure is deliberately injected at the renderer boundary; no paid-provider uptime is tested.",
        "- generation_time_s measures rendering and audio processing, excluding queue, planning, and validation.",
        "- Output is fixed to 48 kHz / -16 LUFS; unsupported requested targets are rejected rather than silently ignored.",
        "- Narration accepts uploaded WAV identifiers or bounded, DNS/IP-validated remote HTTPS WAV inputs; private destinations require explicit test/internal allowlisting.",
        "", "## Reproduce", "", "```powershell", "python -m pip install -r backend/requirements.txt",
        "python -m pytest -q --basetemp=test_results/pytest-UNIQUE-RUN --junitxml=test_results/pytest-UNIQUE-RUN.xml",
        "npm test", "npm run build", "python scripts/batch_runner.py --report REPORT.md", "```",
        "", "Use a new scratch directory and XML filename for each pytest run. The batch always creates a unique run directory.",
    ])
    report_text = "\n".join(report_lines) + "\n"
    (run / "REPORT.md").write_text(report_text, encoding="utf-8")
    if args.report:
        args.report.write_text(report_text, encoding="utf-8")
    print(json.dumps({"evidence":str(run), "passed":summary["passed"], "failed":summary["failed"]}))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())