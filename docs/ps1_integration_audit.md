# PS1 Integration Audit And Verification

This change set addresses the remaining HTTP/storage/narration/deployment boundaries identified against the supplied IncuBrix brief. It does not certify all must-haves, commercial legal clearance, subjective music quality or public demo availability. Earlier reports, batch artifacts and the filled submission workbook remain historical snapshots.

## Preserved Module 3

`backend/app/services/music_generation.py` was not edited. It matches the pre-audit snapshot retained in the submission evidence pack, SHA-256:

`7d5921d73b70d5fa6577d04da48f445b64620fd44a7a679a0085f51b0ee6c44e`

The shared musical plan, independent procedural rendering, seed handling and canonical stem/master-gain reconstruction remain unchanged. Worker changes are integration orchestration only. Compose explicitly selects `AUDIO_PROVIDER=procedural`; optional CC-BY-NC MusicGen is never selected by default.

## Changes And Evidence

| Boundary | Implementation | Verification |
|---|---|---|
| Every-POST idempotency | Shared APIRoute, mandatory header, atomic SQLite ledger, canonical JSON/multipart fingerprints, persisted response replay, conflict rejection, pending-operation safety | `backend/tests/unit/test_http_idempotency.py`, `tests/integration/test_http_standard.py` |
| S3-compatible outputs | boto3/MinIO adapter, versioned objects, MD5 upload verification, SHA-256 metadata/head checks, signed download redirects, CORS and cache restoration | `tests/integration/test_s3_outputs.py`; S3 protocol emulation executed, live MinIO gated separately |
| Parent/child safety | Existing rollback wraps child snapshot publication; parent result/files survive failures; old S3 versions remain | Version and injected child-publication failure tests |
| Remote narration | Background HTTPS download, DNS/IP checks with pinned address and TLS hostname, no credential URLs, bounded bytes/time/redirects, no compressed response expansion | Actual local HTTP server tests, private/mixed-DNS/redirect/size/corruption/timeout tests |
| Music-only ducking | No narration added to output; actual gain-curve points retained; speech requires existing8-15dB/recovery/continuity checks | Actual PCM reconstructed from manifest curve; old forged-PASS tests remain strict |
| Silent narration | Must measurably match original music within PCM tolerance; explicit warning, not fake attenuation | Current no-speech integration cases |
| Brief section request | start_s mood-change entries normalize to existing start/end ranges;1.5s spacing still enforced | Unit normalization and rejection test |
| Exact OpenAPI | Generated from actual routes, mandatory headers, structured errors, seed/section schemas, binary media and redirect declarations | Exact checked-in versus runtime schema test |
| Errors | Application, validation, framework404/405, unexpected500, database/storage503 envelopes; code catalogue generated into README | Fault injection and schema tests |
| Correlation logs | Safe request IDs, stored job correlation, context across worker threads, emitted records capture context, failed-worker logs retain original ID | Success and failure JSON-log assertions |
| Health/ready | Versioned documented routes; root compatibility aliases; DB, worker and S3 readiness | Dead worker, DB failure and S3 outage tests |
| Startup/tests | MinIO service, procedural-default backend, frontend, separate Python3.13/Node24 test service | Compose config parsed; actual container run blocked by unavailable daemon |

## Final Executed Gates

| Gate | Actual Result | Evidence |
|---|---|---|
| Complete backend/unit/integration/E2E suite | 234 collected:232 passed,0 failures,2 skipped | `test_results/ps1-final-verified.xml` |
| Frontend tests under Node24 | 30 passed,0 failures | `test_results/ps1-frontend-final.xml` |
| Production Vite build under Node24 | Succeeded | Executed build; output in dist |
| Compose syntax | `docker compose config --quiet` succeeded | Executed CLI validation |
| Docker test service/live MinIO | Not executed: daemon connection failed before image/build/start | Explicit command failure below |

The two skipped tests are real MusicGen inference (not opted in; no weights/API/GPU run) and live MinIO (requires the Docker test service). S3-emulator tests use real boto3 request/response behavior but are **not** represented as proof of an actual MinIO deployment. Remote narration tests do execute a real local HTTP server, allowlisted only in the test configuration.

Initial full run:227 passed,1 failure,2 skips. The remaining failure was an older fixture that added voiceover while asserting zero attenuation; it was corrected to write unducked music under the new music-only contract, retaining the same strict falsification assertion. An S3 rollback regression also exposed Windows CRLF versus uploaded LF manifest bytes; the atomic JSON writer now emits identical UTF-8 bytes. Failed artifacts/reports remain available; no tests or validators were weakened to obtain the final result.

## Commands Executed

```powershell
python -B scripts/export_openapi.py
python -B scripts/export_error_codes.py
python -m pytest -q --tb=short --show-capture=no --basetemp=D:/incubrix-tests/ps1-final-verified --junitxml=test_results/ps1-final-verified.xml
& './test_results/npm-cache/_npx/387698761821791d/node_modules/node/bin/node.exe' node_modules/vitest/vitest.mjs run --reporter=junit --outputFile=test_results/ps1-frontend-final.xml
& './test_results/npm-cache/_npx/387698761821791d/node_modules/node/bin/node.exe' node_modules/vite/bin/vite.js build
docker compose config --quiet
docker compose --profile test run --build --rm tests
```

Python was the installed Python3.13 runtime. boto3/Moto/PyYAML were installed in isolated `D:/incubrix-tests/integration-tools` and selected through PYTHONPATH for local checks; normal setup is `python -m pip install -r backend/requirements-test.txt`. D: scratch storage avoids the nearly full Windows C: temporary drive and preserves previous evidence. The Node cache hash above is local to this machine; an installed Node24 can run the same tools directly.

The last Compose command failed with the missing Docker Desktop Linux engine named-pipe error. No container build, live object-store server, compose healthcheck or container test PASS is claimed. After starting Docker Desktop's Linux engine and ensuring adequate disk space, execute:

```powershell
docker compose up --build
docker compose --profile test run --build --rm tests
```

The test service sets `RUN_MINIO_TEST=1` and exercises real signed-object downloads against MinIO. It uses no paid credentials or audio-model weights. Hosted CI was updated to run this gate, but no hosted CI result is claimed here.

## Operational And Remaining Limitations

- Replace local-only MinIO development credentials and use appropriate TLS/public endpoints before deployment. Review MinIO's separate AGPL service obligations; no full commercial licence audit is claimed.
- Idempotency survives process restart. An operation interrupted after effects but before storing its response remains explicitly pending rather than being blindly executed again; operational reconciliation may be required. Keys/responses are retained, not expired automatically.
- Remote URL policy blocks private/link-local/reserved destinations unless explicitly allowlisted. Network/body timeouts are configured, but OS DNS and thread scheduling are not claimed as hard process-kill deadlines.
- JSON request size is bounded; production should also enforce ingress limits/rate limits. Public auth, quotas and hosted availability are outside this local verification.
- Local output/cache storage remains necessary for rendering and revalidation; authoritative S3 versions can restore it. Publication failure never creates a successful job. Multi-file crash consistency under abrupt power loss is not proven.
- Music-only no-speech acceptance is an explicit correct-behavior branch, not an attenuation PASS; actual equality is checked. Real speech robustness and absence of audible pumping/clicks still need listening/corpus evaluation.
- This work does not fix bar-line endings for every fixed-duration/tempo combination, subjective prompt matching, per-stem perceptual bleed, multilingual evaluation, production cost/throughput or missing public demo/video/reviewer access.
- No new music/audio model is made the default, and no live AI generation is claimed. Existing procedural test results must not be relabelled as MusicGen evidence.
- Before updating the submission workbook to Yes, rerun live container/MinIO gates and review every full criterion, including legal and listening requirements. Do not replace the historical snapshots or claim marks automatically.