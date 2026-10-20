# IncuBrix Assessment Evidence Report

Run: `20261020T050433Z_e67b2ebc`. Mode: `full`.

Executed 39 batch checks: 39 PASS, 0 FAIL, 0 skipped.
PASS means the assertions recorded for that case passed. It does not imply overall assessment compliance.

Evidence directory: `E:\Downloads\ai-background-music-engine-main\ai-background-music-engine-main\test_results\20261020T050433Z_e67b2ebc`. Previous run directories are not overwritten.

## Measured Duration Cases

| Test | Requested s | Actual s | Rate Hz | LUFS | True Peak dBTP | Reconstruction dB | Status |
|---|---:|---:|---:|---:|---:|---:|---|
| duration-5 | 5 | 5.0 | 48000 | -15.99999952110389 | -4.777154583528017 | -76.56562783198035 | PASS |
| duration-6 | 6 | 6.0 | 48000 | -15.99999956729267 | -5.048755982025539 | -76.57183444124828 | PASS |
| duration-10 | 10 | 10.0 | 48000 | -15.999999264626886 | -4.65702459657151 | -76.50566220422658 | PASS |
| duration-30 | 30 | 30.0 | 48000 | -16.00000013900279 | -4.240345624286885 | -76.39943083769124 | PASS |
| duration-45 | 45 | 45.0 | 48000 | -16.000000019080108 | -4.224255448033316 | -76.40183365750264 | PASS |
| duration-60 | 60 | 60.0 | 48000 | -15.999999557303935 | -4.217567567041575 | -76.40258115495429 | PASS |
| duration-180 | 180 | 180.0 | 48000 | -15.999998855806727 | -4.226500484776399 | -76.41497280930514 | PASS |
| duration-300 | 300 | 300.0 | 48000 | -16.000000243674275 | -4.3904108877215045 | -76.48173856905164 | PASS |

## Regeneration

The regeneration-drums case stores actual before/after SHA-256 hashes, checks all unchanged stem metadata, requires a new mix hash, and remeasures the final WAV and reconstruction.
See `logs/regeneration-drums.json`. Canonical stems already include the boundary mastering fades. Reconstruction uses each stem's gain_db plus mix.master_gain_db.

## Scope And Limitations

- Audio comes from the existing deterministic procedural synthesizer, not an AI audio model. ai_generated is false; provider is local; model and cost are null.
- Ten prompt cases prove generation and technical output validity, not subjective prompt matching or musical quality.
- Song/artist prompts are not reproduced using recordings. This is not a perceptual similarity or copyright-clearance test.
- No external samples are used by the local renderer; commercial legal clearance remains unknown, not PASS.
- Narration fixtures are synthetic speech-like waveforms. Energy-based detection is not robust speech recognition. A no-speech narration job is honestly rejected by the ducking gate.
- Loop evidence measures bar alignment and boundary discontinuity, not subjective seamlessness.
- Two requests may be concurrent, but the existing worker renders its queue serially.
- Provider failure is deliberately injected at the renderer boundary; no paid-provider uptime is tested.
- generation_time_s measures rendering and audio processing, excluding queue, planning, and validation.
- Output is fixed to 48 kHz / -16 LUFS; unsupported requested targets are rejected rather than silently ignored.
- Narration URLs resolve previously uploaded WAV identifiers locally. Arbitrary remote downloads are not implemented.

## Reproduce

```powershell
python -m pip install -r backend/requirements.txt
python -m pytest -q --basetemp=test_results/pytest-UNIQUE-RUN --junitxml=test_results/pytest-UNIQUE-RUN.xml
npm test
npm run build
python scripts/batch_runner.py --report REPORT.md
```

Use a new scratch directory and XML filename for each pytest run. The batch always creates a unique run directory.
