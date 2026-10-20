# Module 8 final artifact audit — 2026-10-19

## Final continuation result — 14:26 UTC (supersedes the historical verdict below)

**Module 8: PASS for the final measured application artifacts and required test
suite. The previously documented fade false-negative blocker is resolved.**

### A. Changes in this continuation

Only the fade measurement and its validator call site changed in production:

- `quality_checks.py`: optional actual-stem PCM reference for mix-boundary
  measurement; fit duration, attenuation exponent, and scalar gain to actual
  waveform samples over 200ms. The optimizer does not receive expected fade
  durations or persisted validation values.
- `validator.py`: supply the unmodified actual stem sum as a carrier reference.
  It is not re-rendered, faded, padded or normalized for this measurement.
- Added adversarial fade regressions and a read-only boundary probe. Existing
  tests/assertions, renderer, thresholds, and unrelated code are unchanged.
- A fitted ramp must explain observable local attenuation above PCM16
  quantization noise and match the waveform within -40dB residual; silence,
  unrelated reference audio, and optimizer convergence alone cannot pass.
- Interior reference level retains the original meaning of the amplitude gate;
  low ramp RMS is not substituted for the interior musical level.

Standalone calls without reference audio retain the boundary-envelope estimator.
The application validator uses actual stem/mix PCM so natural musical decay
cannot masquerade as the applied mix-master fade duration.

### B. Reproduced root cause

With the original current code, rejected artifact
d6ae2e5c-08da-4bef-a680-875ba9f963e5 measured fade-out **19.333ms**. Inspecting
its final 200ms showed mix-to-stem gain approximately **0.3634** before the last
10ms. Its envelope had already dipped naturally 18–19ms before EOF. The old
20–40ms plateau threshold therefore measured musical dynamics, not the applied
attenuation ramp. Final reference-based measurement is **9.998ms in / 9.967ms
out** on that same observed PCM, without modifying the artifact.

### C. Final verification (in requested order)

| Suite | Final result |
| --- | --- |
| Focused fade tests | **19 passed** |
| Module 8 unit/integration + PS1/ducking/looping/API regressions | **88 passed** |
| Entire backend | **106 passed** |

[Regression XML](data/module8-fade-regressions.xml) and
[full suite XML](data/module8-fade-full-suite.xml) retain final results. Only an
existing Starlette/httpx deprecation warning remains. Added tests reject absent,
0.5ms/1ms, 30ms/150ms/333ms ramps, unrelated references and silent edges; valid
10ms ramps, quantized anti-phase stereo and the quiet ambient pipeline pass.

### D. Fresh post-test artifact evidence

Actual JobWorker generated job **b4f1b9f0-11e4-4707-a768-8400148115a9** after the
final tests, at **2026-10-19T14:26:43Z**. Real child workers created the looped and
ducked WAVs; a newly spoken voiceover was serialized and measured.

- [Fresh mix WAV](audio/generated/b4f1b9f0-11e4-4707-a768-8400148115a9/mix.wav)
- [Complete final evidence](data/module8-audit-08a42d8e-1824-4dd5-a644-5e82ec0fb8b7/report.json)
- [Independent FFmpeg check](data/module8-audit-08a42d8e-1824-4dd5-a644-5e82ec0fb8b7/ffmpeg-ebur128.txt)

| Actual measurement | Result |
| --- | --- |
| Duration / error | 45.000000s / 0.000000s |
| Sample rate / frames | 48000Hz / 2160000 |
| WAV subtype / depth / channels | PCM_16 / 16 bits / 1 |
| LUFS | -15.99999983 |
| True peak | -3.01580346dBTP |
| Clipped sample count | 0 |
| Measured mix-master fade-in | 9.994ms |
| Measured mix-master fade-out | 9.989ms |
| Stem count | 4: drums, bass, piano, pads |
| Stem duration / alignment | Each 45s, 2160000 samples at 48000Hz; exact mix alignment |
| Mastered stem reconstruction error | -75.722dB |
| Ducking attenuation / recovery | 12.000dB / PASS |
| Ducking transition maximum delta | 0.040741 |
| Loop interval | 40.000000–42.666667s |
| Actual loop-boundary jump | 0.000000 |
| Required outputs | All 8 exist and decode/read successfully |
| Final validation | PASS |

Independent FFmpeg output corroborates -16.0LUFS and approximately -3.0dBTP.
An independent stem/mix fade search finds 10.000ms at each boundary. Forged
metadata/returned stem lists and permissive renderer configuration do not change
the measured checks. SHA256 fingerprints are retained in the evidence report.

### E. Final status

**PASS**, based on this newly generated artifact and final validator results,
not on prior artifacts, cached PASS flags, manifest claims or renderer settings.
The historical failures below are preserved as investigation history, not the
current sign-off result.

---

## Historical audit before the continuation fix

## Verdict

**Fresh 45-second artifact: PASS. Overall Module 8 sign-off: BLOCKED.**
The full backend suite still has 17 failures. Do not interpret the passing fresh
artifact or focused unit suite as proof that the validator is reliable for all
rendered musical material.

No renderer or frontend code was changed. No existing test assertion was removed.
The earlier fade-fit relaxation from 0.5 to 0.6 was reverted: the reversed tail
was incorrectly compared to a falling reference. Both orientations now compare
the edge-to-interior rising ramp with the original 0.5 gate.

## Evidence-source audit

| Decision | Observed evidence | Permitted comparison target / remaining input |
| --- | --- | --- |
| Duration | Decoded mix sample count / actual WAV rate | Specification duration; fixed 0.05-second tolerance |
| Sample rate | WAV header | Fixed 48000 Hz, not renderer configuration |
| Format, depth, channels | WAV container/subtype/channel header | WAV PCM16, supported mono/stereo |
| Loudness | Rendered PCM metered at its actual rate | Fixed -16 LUFS ±1 LU |
| True peak | Rendered PCM with fixed 4x polyphase oversampling | Fixed -1 dBTP limit |
| Clipping | Actual PCM samples | Fixed 0.9999 full-scale proximity threshold; zero clipped samples |
| Fades | Rectified boundary envelopes, not configured fade labels | Fixed 10ms targets; original 7.5ms estimator tolerance and 0.5 fit gate |
| Stems | Actual stem WAVs and mix sample count, rate, channels | Expected identities from specification, not result/manifest stem list |
| Reconstruction | Actual, complete stem PCM against unmodified actual mix PCM | Documented mastering model; error at most -40 dB |
| Ducking | Original mix, ducked mix, actual voiceover PCM; redetected speech | Voiceover source locator and original operation enablement only; 8–15 dB attenuation |
| Looping | Exact selected tail/head samples in actual looped WAV; actual mix dimensions | Original requested musical grid; endpoints must be in bounds and match that grid; no clamping |
| Required outputs | File existence, WAV decoding, finite/nonempty samples, readable JSON | Which operations were requested determines required filenames |
| Manifest | Existence/readability only for output completeness | Descriptive metadata; no measured audio-property evidence |

Targets necessarily remain inputs: requested duration defines *what* is tested,
and loop/voiceover source intent defines *where/which file* to test. None supplies
the measured result. Original child-operation inputs retain required duck/loop
checks even if cached parent validation data is removed. Source audio must remain
available; missing source files fail closed rather than falling back to cached
measurements.

### Confirmed fixes

- Acceptance policy isolated from mutable renderer/environment settings.
- Loudness measured at actual WAV rate without resampling the observed mix.
- Corrected reversed fade reference and avoided anti-phase channel cancellation.
- Stems aligned against the mix, not merely against one another.
- Removed fitting/truncation of observed audio and silent repair of short stems
  before reconstruction comparison.
- Reconstruction rejects sample/channel shape differences instead of comparing
  only the shorter prefix.
- Missing, unreadable, empty, non-finite and invalid-format artifacts fail closed.
- Loop bounds are rejected rather than clamped; stored jump, duration, status,
  and alignment flags are not measurements.
- Ducking recomputes attenuation, recovery and transitions from all three source
  WAVs; the recovery window starts after the standard 300ms release.
- Audio-validation, loop/duck-validation and audio-related PS1 report decisions
  remeasure current files instead of reporting stale persisted audio PASS data.

PS1 commercial licensing, section labels, prompt variety and musical-structure
descriptions remain their existing, separate metadata/benchmark features. This
audit does not claim audio-only proof for those unrelated PS1 requirements.

## Final tests (project virtual environment)

| Group | Result | Evidence |
| --- | --- | --- |
| A: Module 8 units plus integration | 40 passed (38 unit + 2 integration) | [XML](data/module8-focused-suite.xml) |
| B: PS1 + ducking + looping + API | 21 passed, 15 failed | [XML](data/module8-regression-suite.xml) |
| C: Entire backend | 77 passed, 17 failed | [XML](data/module8-full-suite.xml) |

All runs retain the existing assertions and failures. One Starlette/httpx
deprecation warning remains. Global Python lacks the multipart upload dependency;
the project virtual environment has the required dependencies.

## Final fresh artifact, generated after A/B/C

- Job: f3273e6b-f4fe-4a11-adf4-49e0ade0fa4b
- Created: 2026-10-19T14:03:30Z
- Generation: actual JobWorker generation → stem processing → mastering →
  validation; actual child workers produced ducked and looped WAVs.
- Voiceover: newly spoken macOS speech synthesis, serialized as a real PCM16 WAV;
  its speech windows were redetected from that WAV.
- [Actual mix](audio/generated/f3273e6b-f4fe-4a11-adf4-49e0ade0fa4b/mix.wav)
- [Full measured evidence, fingerprints and checks](data/module8-audit-f20f3c43-7d8e-413f-a2e4-2a2a12c51784/report.json)
- [Independent FFmpeg EBU R128 output](data/module8-audit-f20f3c43-7d8e-413f-a2e4-2a2a12c51784/ffmpeg-ebur128.txt)
- [Actual voiceover](data/module8-audit-f20f3c43-7d8e-413f-a2e4-2a2a12c51784/voiceover.wav)

| Measured property | Result |
| --- | --- |
| Duration | 45.000000 s |
| Duration error | 0.000000 s |
| Frames / sample rate | 2160000 / 48000 Hz |
| Container / subtype / depth | WAV / PCM_16 / 16 bits |
| Channels | 1 |
| Integrated loudness | -15.99999983 LUFS |
| True peak, 4x | -3.01580346 dBTP |
| Clipped samples | 0 |
| Boundary-envelope fade-in estimate | 12.062 ms |
| Boundary-envelope fade-out estimate | 15.062 ms |
| Stems | drums, bass, piano, pads: 4 actual files |
| Stem durations/alignment | Each 45.000000 s / 2160000 samples / 48000 Hz; all match mix |
| Mastered stem reconstruction error | -75.722 dB |
| Ducking attenuation | 12.000 dB |
| Ducking transition max delta | 0.040741; recovery passed |
| Loop interval | 40.000000–42.666667 s |
| Loop seam jump | 0.000000 normalized amplitude |
| Required files | All 8 exist and are readable |
| Artifact overall result | PASS |

FFmpeg independently reports -16.0 LUFS and -3.0 dBTP (its displayed precision).
Forging cached audio/manifest PASS values, attenuation/jump/alignment claims and
the returned stem list left the measured checks unchanged. Supplying a permissive
renderer configuration also left them unchanged. The evidence includes SHA256
fingerprints for each WAV, the voiceover and the manifest.

## Reconstruction and independent fade diagnosis

The renderer's serialized mix is **not an unmastered raw sum**. Stem files already
have a boundary fade; rebuilding the mix adds another, and final processing adds
one more plus global mastering gain. Validation models that existing transform
on the actual stem PCM, without changing the observed mix. A separate audit-only
least-squares check uses raw stem sum × two additional 10ms mix fades × a fitted
global scalar. It independently matches the final actual mix at -75.72205 dB.
Searching 1–80ms rather than inserting the requested duration fits 10.000ms at
both ends. This corroborates the rendered boundary ramps; it does not replace the
validator's envelope check or force its result to PASS.

## Remaining blocker: fade estimator false negatives

The regression failures include generation jobs rejected at the fade check,
which then block dependent API/loop/duck/PS1 tests. A concrete rejected artifact
is job d6ae2e5c-08da-4bef-a680-875ba9f963e5:

- Validator: fade-out estimate **19.333ms**, exceeding its unchanged 17.5ms upper
  bound; fade-in 9.938ms. Other checks in its saved validation passed.
- Independent fit against that artifact's actual stem/mix PCM: **10.000ms**
  fade-in and fade-out; reconstruction error **-76.07155dB**.
- Attribution for this reproduced case: **validator estimation error**, not
  evidence that the renderer omitted or lengthened its applied 10ms ramps.

The local plateau/crossing heuristic remains sensitive to the music's natural
envelope. It has not been rewritten or granted wider thresholds during this
audit. The demonstrated fresh 45-second artifact is compliant, but Module 8
cannot receive an unrestricted final sign-off until the remaining estimator
false negatives and the resulting regression failures are resolved.