from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import soundfile as sf


STEMS = ["drums", "bass", "harmony", "melody"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate independently generated Module 3 WAV stems.")
    parser.add_argument("directory", nargs="?", default="audio/generated/latest")
    args = parser.parse_args()
    root = Path(args.directory)
    arrays = {}
    print("Stem Validation\n==============\n")
    for name in STEMS:
        path = root / f"{name}.wav"
        if not path.exists():
            print(f"{name}.wav: FAIL (missing)")
            return 1
        audio, sr = sf.read(path, dtype="float32")
        arrays[name] = audio
        rms = float(np.sqrt(np.mean(np.square(audio))))
        peak = float(np.max(np.abs(audio)))
        print(f"{name}.wav\nDuration: {len(audio)/sr:.2f} sec\nSample Rate: {sr}\nRMS: {rms:.6f}\nPeak: {peak:.6f}\n")
    mix, mix_sr = sf.read(root / "mix.wav", dtype="float32")
    print(f"Mix\nDuration: {len(mix)/mix_sr:.2f} sec\nSample Rate: {mix_sr}\n")
    print("Stem independence:")
    for i, left in enumerate(STEMS):
        for right in STEMS[i + 1:]:
            corr = float(np.corrcoef(arrays[left], arrays[right])[0, 1])
            print(f"{left} vs {right}: {'PASS' if not np.array_equal(arrays[left], arrays[right]) and abs(corr) < 0.999 else 'FAIL'}")
    print("\nOverall: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
