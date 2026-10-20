# IncuBrix PS1 - AI Background Music Engine

## Modules 9-10: Storage And Verifiable Assessment

### Integration Contract Update

Every POST now requires `Idempotency-Key`, including uploads, specifications, cancellation and validation. Same key/method/path/query/body replays the stored response; changed payload returns409. Multipart boundaries do not affect file fingerprints. Interrupted pending operations return `IDEMPOTENCY_IN_PROGRESS` rather than executing twice.

```powershell
docker compose up --build
docker compose --profile test run --build --rm tests
```

Startup includes versioned MinIO storage. The test service uses Python3.13/Node24 and runs backend unit/integration/E2E, live MinIO, frontend tests and build in one command. No paid API keys or model weights are required. Compose always selects procedural audio, never MusicGen. MinIO runtime verification requires a running Docker daemon; S3-emulator tests are not MinIO deployment proof. Bundled credentials are local-development-only, object ports bind to loopback, and production must override credentials and public endpoint/TLS.

`STORAGE_BACKEND=s3` publishes hash-verified object versions before success; result links redirect to short-lived signed downloads. Local WAVs remain the render cache and canonical reconstruction is unchanged. Packages restore missing cached objects. Prior versions survive regeneration; child publication failures roll back parent files/results. MinIO is a separate service, not code embedded in the application.

Remote HTTPS narration uses byte/time/redirect bounds, checked public DNS addresses and pinned destination IP/TLS hostname. Private/loopback/link-local addresses are blocked unless explicitly allowlisted for trusted internal tests. Ducked output is music-only. Its manifest records actual linear-gain curve points. No-speech output must match original music within PCM tolerance; ordinary speech still requires8-15dB reduction/recovery.

Brief-format `start_s` section changes normalize to existing start/end ranges with the unchanged1.5s spacing rule. Module3 shared planning/rendering is not changed. OpenAPI is generated and tested for exact parity. `/api/v1/health` and `/api/v1/ready` are documented; root aliases remain for compatibility. Readiness checks database, worker and configured object storage. Correlation/job context propagates into JSON logs across worker threads, without logging URL query strings.

These integration fixes do not certify subjective musical quality, legal commercial clearance, public demo uptime, cost benchmarks or live AI fidelity. Earlier reports/submission workbooks remain historical snapshots.

The current backend includes isolated artifact storage, measured manifests, single-stem regeneration with rollback, and automated evidence collection. The original module notes below are historical; the standard jobs endpoint now plans and renders real audio instead of simulated success.

**Important:** the existing audio renderer is procedural synthesis, not an AI audio model. This is a remaining assessment limitation. The manifests state this explicitly; passing technical tests is not a claim of full IncuBrix compliance.

### Run On Windows

From the project root, with Python dependencies installed:

```powershell
python -m pip install -r backend/requirements.txt
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8001
```

In another terminal:

```powershell
npm install
$env:VITE_API_BASE_URL = "http://127.0.0.1:8001"
npm run dev
```

Open http://localhost:5173/. API docs: http://127.0.0.1:8001/docs. Run only one instance per port. Restart the backend after source changes unless using `--reload`.

### Storage Contract

`AUDIO_OUTPUT_DIR` defaults to `./outputs`, resolved from the backend process working directory. Each job has an isolated directory containing `mix.wav`, requested `mix.mp3`, `music_manifest.json`, and canonical `stems/*.wav`. Narration operations create `mix_ducked.wav`; looping creates `mix_looped.wav`. Existing flat stem files and `manifest.json` remain for Modules 1-8 compatibility. Previous directories are not deleted or migrated; set `AUDIO_OUTPUT_DIR` to a prior storage root when serving an older database whose artifacts are there.

Canonical stems include the existing mix-boundary fades so reconstruction is linear: sum stems with their `gain_db`, then apply `mix.master_gain_db`. Their hashes and metadata remain unchanged when another stem is regenerated. Regeneration rebuilds the mix, derivatives, validation, MP3, and manifest; failed child operations roll back the parent. Successful child jobs retain an isolated snapshot.

Artifact and manifest endpoints:

- `POST /api/v1/music/jobs`: prompt-to-artifacts pipeline (5-300 seconds).
- `GET /api/v1/jobs/{job_id}`: asynchronous state and result.
- `GET /api/v1/music/jobs/{job_id}/manifest`: measured manifest with verified file references/hashes.
- `GET /api/v1/music/jobs/{job_id}/mix`: WAV; `.../mix.mp3`: requested MP3.
- `GET /api/v1/jobs/{job_id}/download/package`: ZIP with canonical stems and manifest.
- `POST /api/v1/music/jobs/{job_id}/stems/{stem_id}/regenerate`: isolated stem update.

Optional fields are `sections`, `tempo_bpm`, `key`, `seed`, `loopable`, `narration_url`, and `output`. Output accepts48000Hz,-16LUFS,WAV and optional MP3. Unsupported targets are rejected. Narration accepts uploaded WAV locators or bounded address-validated remote HTTPS WAV URLs. No-speech output is checked for unchanged music, not claimed as proven speech attenuation.

```json
{"prompt":"Cinematic hopeful instrumental music","duration_s":45,"seed":42,"output":{"sample_rate":48000,"target_lufs":-16,"formats":["wav","mp3"]}}
```

### Tests And Evidence

Use Node 24 for frontend tests/builds. On this machine Node 22.14.0 could not start the installed jsdom/undici test dependencies; the complete 30-test frontend suite passed with an isolated Node 24 runtime. This does not require changing application dependencies.

```powershell
python -m pytest -q --basetemp=test_results/pytest-UNIQUE-RUN --junitxml=test_results/pytest-UNIQUE-RUN.xml
npm test
npm run build
python scripts/batch_runner.py --report REPORT.md
```

Choose a fresh pytest scratch path/XML name for each run. This avoids filling the Windows system temporary drive and preserves earlier reports. Test fixtures are deterministic synthesized signals, not fake production evidence. The batch runs an isolated in-process FastAPI application with its real worker and renderer; it does not contact or alter your running development server.

Every batch creates `test_results/{UTC_timestamp}_{random_id}/` containing `summary.json`, `summary.csv`, `REPORT.md`, `logs/`, `outputs/`, `manifests/`, and its SQLite database. It exits nonzero on failure. `--quick` runs only short-duration and 45-second regeneration checks and is not full assessment coverage. Full runs consume significant disk space because all WAVs and prior evidence are retained.

See `docs/assessment_test_matrix.md`, generated `REPORT.md`, `SOURCES.md`, and `AI_USE.md` for evidence scope and limitations. GitHub Actions runs the complete backend/frontend suites and full deterministic batch without paid secrets. The report is generated only from actual batch records; no benchmark numbers are embedded in it.

See `docs/submission_verification.md` for the final executed commands, exact file inventory, test totals, and retained evidence locations. To regenerate the complete API contract, run `python scripts/export_openapi.py`; the resulting `openapi.yaml` uses JSON syntax, which is a valid YAML subset.

## Historical Module Notes

This repository contains the existing IncuBrix PS1 frontend plus the completed Module 1 backend.

> **Scope:** Module 1 only. The real AI music generation engine, stems, voiceover ducking, looping, and audio processing are intentionally not implemented yet.

## Architecture

```text
React + Vite frontend
        |
        | POST /api/v1/music/jobs
        v
FastAPI API
        |
        +--> SQLite job store
        |
        v
Async local job worker
        |
        v
queued -> processing -> succeeded/failed/cancelled
```

The worker exposes a swappable `process_music_job(job_id)` boundary. It currently simulates processing and does not generate audio.

## Module 1 features

- `POST /api/v1/music/jobs` returns HTTP 202 and a `job_id` immediately.
- `GET /api/v1/jobs/{job_id}` returns status and progress.
- `POST /api/v1/jobs/{job_id}/cancel` supports queued/processing cancellation.
- Strongly typed job states and validated transitions.
- SQLite persistence.
- Idempotent POST requests with `Idempotency-Key`.
- Structured JSON errors using `code`, `message`, `retryable`, and `details`.
- `GET /health` and `GET /ready`.
- JSON structured logs with correlation IDs.
- FastAPI `/docs` and `/redoc`.
- React frontend integration with controlled polling and user-friendly errors.

## API contract

The source-of-truth contract is `openapi.yaml`. FastAPI also generates `/docs` and `/redoc` from the implementation.

### Create a job

```http
POST /api/v1/music/jobs
Idempotency-Key: demo-001
Content-Type: application/json

{"prompt":"Calm cinematic background music","duration_s":45}
```

Response: `202 Accepted`

```json
{"job_id":"<uuid>","status":"queued"}
```

PS1's input contract allows `5` to `300` seconds at the API boundary; the Core evaluation requires successful operation across 10 seconds to 3 minutes. The current Module 1 API validates the documented 5–300 second boundary.

### Get status

```http
GET /api/v1/jobs/{job_id}
```

Example:

```json
{
  "job_id":"<uuid>",
  "status":"processing",
  "progress":60,
  "created_at":"...",
  "updated_at":"...",
  "prompt":"Calm cinematic background music",
  "duration_s":45,
  "error":null,
  "result":null
}
```

### Cancel

```http
POST /api/v1/jobs/{job_id}/cancel
```

A terminal job cannot be cancelled and returns a structured `409` error.

## Idempotency

If a client sends an `Idempotency-Key`, the backend hashes the logical request (`prompt` + `duration_s`). Repeating the same key with the same payload returns the original job and does not create duplicate work. Reusing the key with a different payload returns `409 IDEMPOTENCY_CONFLICT`.

## Error format

```json
{
  "error": {
    "code": "INVALID_DURATION",
    "message": "duration_s must be between 5 and 300 seconds.",
    "retryable": false,
    "details": {"min":5,"max":300}
  }
}
```

Validation, not-found, conflict, and unexpected-server errors use this envelope. Tracebacks are never returned to clients.

## Run locally on Windows

### Backend

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

Health: http://localhost:8000/health

### Frontend

Open another terminal at the project root:

```powershell
npm install
npm run dev
```

Frontend: http://localhost:5173

Set `VITE_API_BASE_URL=http://localhost:8000` in a root `.env` file if the API runs somewhere other than the default. Never commit real secrets.

## Docker

For the assessment-style reproducible setup, build and start both services with:

```powershell
docker compose up --build
```

Then open http://localhost:5173 and the API at http://localhost:8000/docs.

Run the backend test suite in one command:

```powershell
docker compose run --rm backend pytest -q
```

The database is persisted in the `jobs-data` Docker volume. No API keys are required for Module 1.

## Tests

```powershell
cd backend
.venv\Scripts\activate
pytest -q
```

The test suite covers health/readiness, creation, validation, status lookup, asynchronous success, idempotency, conflicts, cancellation, and invalid terminal-state cancellation.

## Future modules

Module 1 deliberately does not generate audio. Future work can replace the worker's mock processing with a configured provider adapter, followed by stem generation, audio processing, validation, manifest creation, and output storage.

## Honest scope

**Module 1 — API & Job Management is implemented. The actual music generation engine is not included yet.**

## Module 2 — Prompt → Music Specification

Module 2 adds a separate deterministic specification boundary:

```text
Natural-language prompt
        ↓
LLM / local fallback
        ↓
JSON
        ↓
Pydantic validation
        ↓
Validated MusicSpecification
        ↓
Downstream music engine
```

### Endpoint

```http
POST /api/v1/specifications
Content-Type: application/json

{"prompt":"I want peaceful music for a nature documentary, gradually becoming hopeful."}
```

The response is:

```json
{
  "specification": {
    "genre": "cinematic",
    "mood": ["peaceful", "hopeful"],
    "tempo": 90,
    "key": "C major",
    "instruments": ["piano", "soft pads", "bass", "light percussion"],
    "sections": [
      {"start": 0, "end": 20, "mood": "peaceful"},
      {"start": 20, "end": 45, "mood": "hopeful"}
    ]
  }
}
```

### LLM configuration

Module 2 supports an OpenAI-compatible chat-completions endpoint. Set these values in `backend/.env` for a real LLM:

```env
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=your-key
LLM_MODEL=gpt-4o-mini
LLM_TIMEOUT_SECONDS=30
DEFAULT_DURATION_SECONDS=45
```

`LLM_PROVIDER=auto` is the default. It uses the configured LLM when `LLM_API_KEY` is present; otherwise it uses a deterministic local fallback so the complete application and tests can run without credentials. No API key is ever sent to the React frontend.

### Module 2 validation

The backend rejects empty prompts, invalid JSON, invalid BPM (40–240), invalid musical keys, empty mood/instrument arrays, invalid section ranges, overlapping sections, and specifications that exceed the configured maximum duration.

### Module 2 tests

```powershell
cd backend
.venv\\Scripts\\activate
pytest -q
```

The frontend has a dedicated **Music Specification** page. It calls `POST /api/v1/specifications` and renders only the validated JSON specification, including genre, mood, tempo, key, instruments, and timeline sections.

## Module 2 — LLM provider configuration

Module 2 is provider-neutral. The backend can use OpenAI, Hugging Face Inference Providers, a local Ollama model, any OpenAI-compatible endpoint, or the deterministic mock fallback. The LLM is used only for prompt interpretation; the validated `MusicSpecification` is the downstream contract.

### Recommended free/open-weight setup: Hugging Face

Hugging Face provides an OpenAI-compatible router and supports open-weight models such as `openai/gpt-oss-20b`. The hosted service has a free tier/allowance for eligible users, but it is quota-limited rather than unlimited. https://huggingface.co/docs/inference-providers/index

Create a fine-grained Hugging Face token with permission to make calls to Inference Providers, then put it in `backend/.env`:

```env
LLM_PROVIDER=huggingface
HF_BASE_URL=https://router.huggingface.co/v1
HF_API_KEY=hf_your_token_here
HF_MODEL=openai/gpt-oss-20b:fastest
LLM_TIMEOUT_SECONDS=60
```

Hugging Face documents `openai/gpt-oss-20b`/`120b` through the OpenAI-compatible chat-completions interface. https://huggingface.co/docs/inference-providers/guides/gpt-oss

### Completely local option: Ollama

Install Ollama, then pull a small local model:

```powershell
ollama pull qwen2.5:7b
```

Set:

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
OLLAMA_MODEL=qwen2.5:7b
OLLAMA_ENABLED=true
```

No cloud API key is required for Ollama. The model runs on your machine.

### Automatic provider selection

You can leave:

```env
LLM_PROVIDER=auto
```

The backend tries configured providers in this order:

```text
Hugging Face → OpenAI → Ollama → deterministic mock
```

The mock fallback is useful for tests and development. It does not pretend to be an LLM: it deterministically maps common music words to the same validated specification shape.

### Important security note

Never commit `backend/.env`, API keys, or tokens. If a real API key has been exposed in a chat, terminal screenshot, Git history, or repository, revoke/rotate it before continuing.

## Module 5 — Audio Processing

Module 5 is integrated into the existing Modules 1–4 background-job pipeline. The generator may render at 44.1 kHz, while the processing layer converts final stems and the mix to 48 kHz WAV, measures duration, measures integrated LUFS, checks true peak using oversampling, handles clipping, applies short endpoint fades, and validates the serialized WAV output.

Defaults:
- Final sample rate: 48,000 Hz
- Mix loudness target: -16 LUFS
- Loudness tolerance: ±1 LUFS
- True peak limit: -1 dBTP, with a small quantization safety margin during limiting
- Duration tolerance: ±0.1 s
- Fade-in/out: 10 ms

### Run locally on Windows PowerShell

Backend:

```powershell
cd E:\Downloads\incubrix-module-4-stem-management-updated
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
$env:PYTHONPATH = "backend"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Frontend (open a second PowerShell):

```powershell
cd E:\Downloads\incubrix-module-4-stem-management-updated
npm install
npm run dev
```

Open `http://localhost:5173` and API docs at `http://localhost:8000/docs`.

### Test

```powershell
cd E:\Downloads\incubrix-module-4-stem-management-updated
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "backend"
pytest -q backend\tests
```

The regression suite covers Modules 1–4 plus Module 5 audio processing and stem-independence behavior.

### Module 5 validation endpoint

After a successful generation job:

```powershell
Invoke-RestMethod "http://localhost:8000/api/v1/music/jobs/<JOB_ID>/validation" | ConvertTo-Json -Depth 10
```

The parent job result also contains `audio_validation` and `stems_validation`.

## Structured Error Codes

<!-- ERROR_CODES_BEGIN -->
`AI_DURATION_UNSUPPORTED`, `AI_INCOMPLETE_AUDIO`, `AI_INVALID_AUDIO`, `AI_INVALID_STEMS`, `AI_MODEL_TERMS_REQUIRED`, `AI_PROVIDER_FAILED`, `AI_PROVIDER_MISMATCH`, `AI_PROVIDER_TIMEOUT`, `AI_PROVIDER_UNAVAILABLE`, `ARTIFACT_CONFLICT`, `ARTIFACT_CORRUPT`, `ARTIFACT_HASH_MISMATCH`, `ARTIFACT_MISSING`, `ARTIFACT_NOT_FOUND`, `ARTIFACT_NOT_READY`, `AUDIO_NOT_FOUND`, `AUDIO_NOT_READY`, `AUDIO_VALIDATION_FAILED`, `HTTP_ERROR`, `IDEMPOTENCY_CONFLICT`, `IDEMPOTENCY_IN_PROGRESS`, `INSTRUCTION_TOO_LONG`, `INVALID_ARTIFACT_PATH`, `INVALID_AUDIO`, `INVALID_AUDIO_PATH`, `INVALID_AUDIO_PROVIDER`, `INVALID_BEATS_PER_BAR`, `INVALID_BPM`, `INVALID_DURATION`, `INVALID_FILE`, `INVALID_IDEMPOTENCY_KEY`, `INVALID_LLM_JSON`, `INVALID_NARRATION`, `INVALID_SAMPLE_RATE`, `INVALID_SECTION_DURATION`, `INVALID_STATE_TRANSITION`, `INVALID_STEM`, `JOB_NOT_FOUND`, `LLM_UNAVAILABLE`, `LOOP_VALIDATION_FAILED`, `METHOD_NOT_ALLOWED`, `MP3_ENCODING_FAILED`, `MUSIC_GENERATION_FAILED`, `MUSIC_NOT_FOUND`, `MUSIC_NOT_READY`, `NARRATION_DOWNLOAD_FAILED`, `NARRATION_NOT_FOUND`, `NARRATION_TIMEOUT`, `NARRATION_TOO_LARGE`, `NARRATION_URL_REJECTED`, `NOT_FOUND`, `NOT_READY`, `PACKAGE_NOT_READY`, `PROMPT_TOO_LONG`, `REQUEST_TOO_LARGE`, `SPECIFICATION_VALIDATION_FAILED`, `STEM_ALIGNMENT_FAILED`, `STEM_NOT_READY`, `STEM_RECONSTRUCTION_FAILED`, `STORAGE_CONFIGURATION_ERROR`, `STORAGE_DOWNLOAD_FAILED`, `STORAGE_PUBLICATION_FAILED`, `STORAGE_UNAVAILABLE`, `VALIDATION_FAILED`, `VALIDATION_NOT_READY`, `VOICEOVER_NOT_FOUND`, `WORKER_RESTART`
<!-- ERROR_CODES_END -->
