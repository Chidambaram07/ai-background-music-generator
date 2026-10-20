# Sources And Provenance

The audio renderer is the existing local procedural synthesizer in `backend/app/services/music_generation.py`. It creates independent instrument-role waveforms; it does not download music, samples, artist recordings, or training datasets. It is not an AI audio model.

| Component | Source | Purpose |
|---|---|---|
| Python standard library | https://docs.python.org/3/ | SQLite, safe paths, hashes, ZIP, timing, evidence serialization |
| FastAPI / Pydantic | https://fastapi.tiangolo.com/ / https://docs.pydantic.dev/ | HTTP jobs and request schemas |
| NumPy | https://numpy.org/ | Synthesis, waveform math, reconstruction |
| SciPy | https://scipy.org/ | Resampling, oversampled true-peak estimation, boundary measurements |
| SoundFile / libsndfile | https://python-soundfile.readthedocs.io/ | Actual WAV/MP3 encoding and decoding |
| pyloudnorm | https://github.com/csteinmetz1/pyloudnorm | Integrated loudness measurements |
| pytest / HTTPX | https://docs.pytest.org/ / https://www.python-httpx.org/ | Real-function tests and ASGI integration tests |
| React / Vite / Vitest | https://react.dev/ / https://vite.dev/ / https://vitest.dev/ | Existing UI, frontend build and tests |

The original Modules 1-8 implementation and its earlier disclosures remain in `backend/SOURCES.md` and `backend/AI_USE.md`.

Dependency versions are recorded at runtime in each new music manifest. Library licences must be reviewed from their distributions before commercial redistribution. Absence of third-party samples is not legal clearance; manifests leave commercial permission unknown.

Optional language-model adapters plan specifications only. The optional MusicGen Melody audio adapter is separately disclosed in docs/audio_provider.md; its CC-BY-NC-4.0 weights are research/non-commercial, opt-in and not the default. Compose explicitly selects procedural audio. Automated deterministic tests make no paid API calls.

## Integration Dependencies

- boto3/botocore: Apache-2.0; https://github.com/boto/boto3/blob/develop/LICENSE . S3/MinIO protocol client; no audio generation.
- HTTPX: BSD-3-Clause; https://github.com/encode/httpx/blob/master/LICENSE.md . Bounded remote narration HTTP client.
- Moto: Apache-2.0; https://github.com/getmoto/moto/blob/master/LICENSE . Test-only S3 emulation; not proof of a live MinIO run.
- MinIO server: AGPL-3.0, run as a separate unmodified container service; https://github.com/minio/minio/blob/master/LICENSE . Commercial use is not synonymous with an absence of licence obligations. Review distribution/network/source obligations before production; no legal-clearance certificate is claimed.

Dependency and model terms still require a complete submission licence review. No research/non-commercial model is selected by the default audio path.