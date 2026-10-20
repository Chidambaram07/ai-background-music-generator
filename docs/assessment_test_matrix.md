# IncuBrix Assessment Test Matrix

Statuses describe the evidence's scope, not a promise of full product compliance. PASS is used only for assertions actually executed. Batch PASS means the functional case passed; it does not prove subjective quality or AI-model generation. `REPORT.md` is generated from real batch records. The latest run's `summary.json` and per-case `logs/*.json` are authoritative for that run.

Final verification: 175 backend tests passed, 30 frontend tests passed under Node 24, and 39 full batch checks passed. No final test failed or was skipped. Exact commands and evidence paths are recorded in `docs/submission_verification.md`. The earlier Node 22 startup failure and C: temporary-disk exhaustion are disclosed there rather than hidden.

| Requirement | Test ID | Test | Expected | Actual Evidence Scope | Evidence | Status |
|---|---|---|---|---|---|---|
| Exact length, including 5/6 and 300 seconds | duration-5 through duration-300 | Read WAV frame count/rate for eight requested durations | Error <= 0.05 s | Actual serialized PCM duration, not requested value | Batch logs, manifests, `tests/e2e/test_assessment_audio.py` | PASS |
| Genuine independent stems | duration-* | At least three independently rendered instrument-role files; compare hashes and align samples | Separate nonidentical tracks, aligned to mix | Four real tracks in standard jobs; renderer has separate drums/bass/harmony/melody functions | WAVs, stem SHA-256, existing engine unit tests | PASS |
| AI audio generation | all generation cases | Inspect actual renderer and provenance | Audio from an AI audio model | Existing renderer is deterministic procedural synthesis; ai_generated=false | `SOURCES.md`, manifests | NOT IMPLEMENTED |
| Instrumental-only | hard-vocals | Render vocal request, inspect permitted instrument roles and output | No vocal audio | No vocal renderer/recordings; heuristic speech detector is not a comprehensive vocal classifier | Batch request/output and source | PARTIAL |
| Commercial-safe generation | hard-song, hard-artist | No third-party recording ingestion; inspect source provenance | No copied song; verified rights | No external recordings or samples; no legal clearance or perceptual-similarity audit | Manifest licences/warnings, `SOURCES.md` | PARTIAL |
| 48 kHz WAV | duration-* | Read actual WAV header | 48000 Hz | Actual header rate | Batch summaries and WAVs | PASS |
| Loudness | duration-* | pyloudnorm meter on final PCM | -16 LUFS +/- 1 LU | Integrated measured LUFS | Batch logs/manifests | PASS |
| True peak | duration-* | Four-times oversampled peak measurement | <= -1 dBTP | Numeric actual-PCM estimate; not hardware certification | Batch logs/manifests | PASS |
| No clipping | duration-* | Count full-scale PCM samples | No clipped samples | Actual sample check | Validator checks, unit fixtures | PASS |
| Intentional ending | duration-* | Fit boundary fade against actual raw stem sum | Observable fade, no accidental discontinuity | Measured boundary attenuation; not a musical-quality listening review | Manifest checks.fades, existing fade tests | PARTIAL |
| Section changes | sections-and-loop | Ordered section request and grid metadata; renderer receives moods | Aligned requested boundaries | Structural timing proven; subjective musical change not scored | Request, music.sections, generated WAVs | PARTIAL |
| Shared tempo/key/grid | sections-and-loop | One specification shared by independent renderers | Shared musical configuration | Renderer grid and metadata, not independent BPM/key estimation from audio | Manifest music, beat/bar times | PARTIAL |
| Voiceover ducking | narration-normal, narration-long-silence | Synthetic speech-like bursts, real ducked WAV and measured reduction | 8-15 dB reduction with recovery | Works for deterministic fixtures; real speech robustness unproven | Ducking checks, WAVs, logs | PARTIAL |
| No-speech narration | narration-no-speech | Silent source through API | No false ducking PASS | Job fails strict speech-required validation with structured error | Error response and logs | PARTIAL |
| Seamless looping | sections-and-loop | Re-read loop WAV and test seam/grid | Bar-aligned bounds and small discontinuity | Numerical continuity measured; listening quality not proven | Loop validation and WAV | PARTIAL |
| Change one part | regeneration-drums | Real 45s generation, SHA-256 before/after, rebuild and revalidate | Only drums changes; others and metadata unchanged | Actual hashes; new mix; new validation/manifest | `logs/regeneration-drums.json`, snapshots | PASS |
| Failed regeneration preserves parent | test_regeneration_failure_rolls_back_parent | Inject failure after mutation | Structured failed child, parent restored | Every original parent file hash equal after rollback | Pytest XML, integration test | PASS |
| Concurrent jobs | concurrent-jobs | Submit two requests concurrently | Isolated valid outputs | Distinct job dirs; worker processes queue serially | Batch logs and outputs | PASS |
| Idempotency | idempotency | Same payload/key twice, compare job and WAV hash | One job, no overwrite | Same job ID and unchanged artifact | Batch record, JobService unit tests | PASS |
| Provider failure path | provider-failure | Deliberately inject timeout at renderer boundary | Failed job, structured error, no successful result | Real worker error path; not paid-provider uptime | Logs and job response | PASS |
| Invalid duration | invalid-duration-* | 0, -1, 301 seconds | HTTP 422, structured errors | Actual API responses | Batch logs and hidden tests | PASS |
| Invalid sections | invalid-sections-1 through -5 | Negative/out-of-range/overlap/short/malformed | HTTP 422, structured errors | Actual request validation | Batch logs, unit tests | PASS |
| Vague/contradictory prompts | hard-vague, hard-contradictory | Render both requests | Valid instrumental outputs with honest defaults | Technical validity; subjective intent resolution unmeasured | Requests, outputs, warnings | PARTIAL |
| Ten different prompts | prompt-* | Generate ten requests and measure final PCM | All output contracts valid | End-to-end generation proven; subjective genre matching not scored | Requests, WAVs, manifests | PARTIAL |
| Storage and manifest | test_complete_pipeline_and_manifest | File/header/hash checks, package extraction | Real references and measurements | Measured manifest and canonical packaged stem hashes | Integration XML, manifests/ZIP tests | PASS |
| Missing/corrupt artifacts | test_storage_measures_and_rejects_bad_artifacts; test_corrupt_download_has_structured_error | Missing WAV/corrupt bytes/path traversal | Structured rejection, never fake success | Actual filesystem mutations | Unit/integration XML | PASS |
| WAV and MP3 export | duration-* | Encode, decode, and package requested formats | Playable outputs, request respected | WAV measured; MP3 decode checked, no separate MP3 loudness claim | Batch outputs, download tests | PASS |
| Evidence preservation | batch run IDs | Run batch repeatedly with timestamp+random IDs | Earlier evidence survives | Distinct complete and quick run directories | `test_results/` | PASS |
| Automated CI | assessment.yml | Workflow installed; local commands executed | Fail on required failures, retain evidence | Local execution proven; hosted GitHub run not executed here | `.github/workflows/assessment.yml` | PARTIAL |
| Remote narration URL retrieval | narration_url contract | Only uploaded file locators accepted/resolved | Safe external fetching if offered | Arbitrary remote fetching intentionally not implemented | Schema, README | NOT IMPLEMENTED |

## Interpretation And Remaining Limits

- Synthetic audio fixtures validate math and failure detection. They do not impersonate human listening, real provider output, or real speech-corpus evaluation.
- All measurements come from file headers and PCM samples. Beat/key/section metadata come from the shared renderer specification and are labelled as such.
- Canonical stem reconstruction uses per-stem gain and the manifest's master gain. Boundary fades are baked into canonical stems; legacy flat stems remain for existing APIs and validator compatibility.
- Original outputs and evidence are retained. Child operation snapshots record their parent source. Ordinary exceptions roll parent changes back; abrupt machine/power failure during filesystem mutation is not proven crash-atomic.
- The standard request path selects four supported instrument roles. Explicit `/generate` requests with fewer than three renderable stems fail the final Module 9 gate rather than fabricating additional tracks.
- Unknown cost, audio model and commercial permission remain null. A language-model planner is not an AI audio generator.
- Use sufficient workspace-drive space for complete tests. A Windows C: temporary-drive exhaustion was recorded in the first verification attempt; later tests use explicit E:-based scratch paths, without deleting prior evidence.