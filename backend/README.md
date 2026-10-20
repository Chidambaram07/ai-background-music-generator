# IncuBrix PS1 — AI Background Music Engine

This repository contains the IncuBrix assessment implementation through **Module 3**:

```text
Natural language
      ↓
Module 2 — LLM / deterministic fallback
      ↓
Validated MusicSpecification JSON
      ↓
Module 3 — deterministic local music engine
      ↓
┌────────────┬────────────┬────────────┬────────────┐
│ drums.wav  │ bass.wav   │ harmony.wav│ melody.wav │
└────────────┴────────────┴────────────┴────────────┘
                     ↓
                  mix.wav
```

The assessment brief makes genuine independent stems the hard part: the output must be separate instrument parts that add back up to the final mix, rather than one mixed track split into frequency bands. fileciteturn31file1L100-L114

## What Module 3 does

Module 3 is a **local, deterministic synthesis engine**. It does not call OpenAI or a paid audio-generation API and does not use an LLM to generate audio.

Given a validated Module 2 specification, it:

- parses the musical key and BPM;
- builds a deterministic chord progression;
- generates an independent percussion/drum buffer;
- generates an independent bass buffer from chord roots/fifths;
- generates an independent harmony buffer with sustained chords and voicing/arpeggiation;
- generates an independent melody buffer using scale/chord tones;
- applies section/mood energy changes;
- writes five PCM-16 WAV files at 44.1 kHz;
- verifies duration, sample rate, non-silence and pairwise stem independence;
- sums the four stems into `mix.wav` and prevents clipping.

This deliberately proves the difficult prototype before adding more sophisticated music-production features.

## Project structure

```text
backend/
└── app/
    ├── api/routes/
    │   ├── jobs.py
    │   ├── music.py
    │   └── specifications.py
    ├── core/
    │   ├── config.py
    │   └── llm.py
    ├── schemas/
    │   ├── music.py
    │   └── specification.py
    ├── services/
    │   ├── job_service.py
    │   ├── music_generation.py
    │   └── specification_service.py
    └── workers/
        └── job_worker.py
scripts/
└── validate_stems.py
audio/generated/
└── <job_id>/
    ├── drums.wav
    ├── bass.wav
    ├── harmony.wav
    ├── melody.wav
    └── mix.wav
```

## Module 3 API

### 1. Generate from validated specification

`POST /api/v1/music/generate` returns `202 Accepted` and a job ID.

```json
{
  "specification": {
    "genre": "cinematic",
    "mood": ["peaceful", "hopeful"],
    "tempo": 82,
    "key": "C major",
    "instruments": ["piano", "soft pads", "bass", "light percussion"],
    "sections": [
      {"start": 0, "end": 22.5, "mood": "peaceful"},
      {"start": 22.5, "end": 45, "mood": "hopeful"}
    ]
  }
}
```

Response:

```json
{"job_id":"<uuid>","status":"queued"}
```

The worker retrieves the **validated specification stored with the job**. It does not ask the LLM to generate audio instructions.

### 2. Poll the job

```text
GET /api/v1/jobs/{job_id}
```

When successful, `result` contains the duration, sample rate, four stems, mix and validation information.

### 3. Stream individual audio

```text
GET /api/v1/music/jobs/{job_id}/stems/drums
GET /api/v1/music/jobs/{job_id}/stems/bass
GET /api/v1/music/jobs/{job_id}/stems/harmony
GET /api/v1/music/jobs/{job_id}/stems/melody
GET /api/v1/music/jobs/{job_id}/mix
```

The frontend uses these endpoints for playback and downloads; filesystem paths are never exposed.

## Module 2 → Module 3 demonstration

1. Open **Music Specification**.
2. Enter:
   `I want peaceful music for a nature documentary, gradually becoming hopeful.`
3. Click **Generate Specification**.
4. Review the validated JSON.
5. Click **Generate Music from Specification**.
6. The Module 1 job worker reports real progress while Module 3 renders drums, bass, harmony and melody.
7. When complete, play the final mix or each independent stem.
8. Download the WAV files if needed.

The existing Module 1 job infrastructure remains in place. The original `/api/v1/music/jobs` route is retained for backward compatibility; Module 3 uses the new `/api/v1/music/generate` contract.

## Windows setup

From the repository root:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
```

For Module 2 with your local Ollama setup, use:

```env
LLM_PROVIDER=ollama
OLLAMA_ENABLED=true
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
OLLAMA_MODEL=qwen2.5:7b
LLM_AUTO_FALLBACK_TO_MOCK=true
```

Then start the backend:

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

OpenAPI docs: `http://localhost:8000/docs`

Health: `http://localhost:8000/health`

## Frontend

Open another terminal at the repository root:

```powershell
npm install
npm run dev
```

Open `http://localhost:5173`.

The frontend preserves the existing IncuBrix visual system and adds the Module 3 hand-off and real audio playback to the existing pages rather than replacing the UI.

## Tests

```powershell
cd backend
.venv\Scripts\activate
python -m pytest -q
```

The prototype test suite verifies:

- key parsing;
- BPM/beat calculations;
- 45-second stem generation;
- 44.1 kHz sample rate;
- equal stem duration;
- non-zero audio;
- pairwise stem independence;
- playable mix output;
- existing Module 1/2 tests.

## Stem validation

After generating a job, point the validator at its output directory:

```powershell
python scripts\validate_stems.py backend\audio\generated\<job_id>
```

For the included demonstration output:

```powershell
python scripts\validate_stems.py backend\audio\generated\latest
```

It reports RMS, peak, duration, sample rate and pairwise independence.

## Prototype result

The included C-major peaceful → hopeful example was generated at 45 seconds / 44.1 kHz. All four stems were non-silent, all were the same duration and sample rate, all pairwise independence checks passed, and `mix.wav` was generated from the four buffers.

## Docker

```powershell
docker compose up --build
```

The backend container exposes port `8000`; the frontend exposes port `5173`.

For a production-style deployment, the next hardening steps would be object storage, richer manifest metadata, concurrency limits and a durable queue. The assessment integration standard calls for containerised HTTP services, asynchronous jobs, idempotency, structured errors, configuration through environment variables, output storage, health endpoints and manifests. fileciteturn31file7L500-L535

## Sources and AI use

- `SOURCES.md` documents the local libraries and licensing assumptions.
- `AI_USE.md` documents AI-assisted development for assessment disclosure.
