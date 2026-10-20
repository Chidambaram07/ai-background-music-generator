# Sources / Dependencies

## Runtime libraries

- Python standard library — application logic, file handling and deterministic composition utilities.
- NumPy — numerical audio buffers and signal synthesis.
- SoundFile — WAV read/write through libsndfile.
- FastAPI / Pydantic — HTTP API and validated contracts.
- SQLite — local job persistence.

No external audio-generation API, dataset, sample pack or copyrighted recording is used by the default Module 3 synthesis path. The audio is generated procedurally from mathematical waveforms.

## Module 2

The existing Module 2 provider adapters remain configurable. The local Ollama configuration uses the user-selected `qwen2.5:7b` model for natural-language specification generation. Module 3 does not send audio-generation requests to an LLM.
