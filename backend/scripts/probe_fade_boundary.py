"""Read-only PCM diagnosis of the remaining real rendered fade rejection."""
from pathlib import Path
import json

import numpy as np
import soundfile as sf

from app.services.audio_processing import AudioProcessor
from app.validation.quality_checks import _smooth_envelope, fades_check


def probe(job_dir: Path) -> dict:
    audio, sr = sf.read(job_dir / "mix.wav", dtype="float64")
    paths = sorted(p for p in job_dir.glob("*.wav") if not p.stem.startswith("mix"))
    reference = sum(sf.read(p, dtype="float64")[0] for p in paths)
    envelope = _smooth_envelope(audio, sr)
    tail = audio[-sr // 5:]
    x = reference[-len(tail):]
    block = sr // 1000
    blocks = []
    for ms in (200, 150, 100, 50, 40, 30, 20, 19, 18, 15, 12, 10, 9, 8, 6, 4, 2, 1):
        begin = len(tail) - ms * block
        xx, yy = x[begin:begin + block], tail[begin:begin + block]
        energy = float(np.dot(xx, xx))
        blocks.append({"ms_before_end": ms, "pcm_rms": float(np.sqrt(np.mean(yy ** 2))),
                       "pcm_peak": float(np.max(np.abs(yy))),
                       "smoothed_envelope": float(np.mean(envelope[-ms * block:len(envelope) - (ms - 1) * block])),
                       "mix_to_stem_gain": float(np.dot(xx, yy) / energy) if energy > 1e-14 else None})
    local = envelope[-int(0.04 * sr):]
    plateau = float(np.median(local[:int(0.02 * sr)]))
    return {"artifact": str(job_dir / "mix.wav"), "sample_rate": sr,
            "current_fades": fades_check(audio, sr, AudioProcessor()),
            "tail_plateau_20_to_40ms": plateau, "crossing_threshold": 0.9 * plateau,
            "final_24_pcm_samples": tail[-24:].tolist(), "tail_pcm_blocks": blocks}


if __name__ == "__main__":
    print(json.dumps(probe(Path("audio/generated/d6ae2e5c-08da-4bef-a680-875ba9f963e5")), indent=2))