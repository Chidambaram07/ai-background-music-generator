# Module 8 frontend verification — 2026-10-19

## Scope

Existing Studio QC, loop and ducking panels were corrected without adding a
second page or changing backend code, audio measurements or validation limits.
The UI displays backend decisions; it does not revalidate audio in JavaScript.

## Files changed

- [src/App.tsx](src/App.tsx): authoritative QC rendering, detailed evidence and
  report modal, freshness/error states, parent refresh after operations, removal
  of unconditional success and hardcoded ducking claims.
- [src/services/validation.ts](src/services/validation.ts): presentation-only
  mapping of fresh response/checks/legacy measured fields, explicit unavailable
  states, and backend status translation. No count/threshold PASS heuristics.
- [src/services/musicApi.ts](src/services/musicApi.ts): align optional/nested
  validation and loop response types; bypass HTTP caches for measured reports.
- [src/index.css](src/index.css): small existing-layout adjustments for explicit
  status badges, wrapping and the evidence dialog. Existing theme/navigation
  and components retained.
- [src/Module8.test.tsx](src/Module8.test.tsx): 23 QC regressions including A–G,
  absent data, nested responses, disabled operations, refresh failures, races,
  regeneration and full looping/ducking completion flows.
- [src/App.test.tsx](src/App.test.tsx): keep existing tests compatible with
  asynchronous fresh loop rendering and the additional QC row; correct a
  pre-existing deferred callback TypeScript type. Assertions retained.
- This verification record.

## Bugs fixed

1. Backend stem FAIL was overridden by three-or-more legacy dictionary entries.
   Actual/expected counts, missing/extra stems, readable/exists flags, per-stem
   duration and sample alignment now come from backend checks.
2. Persisted loop PASS overrode fresh measured FAIL. Current Module 8 loop checks
   are preferred; a separate fresh loop endpoint is used only for older responses
   without that check. Persisted job loop status is never a QC fallback.
3. Ducking always claimed ACTIVE -12dB with fake filename/speech regions. The
   existing panel now displays measured attenuation, speech windows, recovery,
   transitions and backend PASS/FAIL, or explicit disabled/unavailable state.
4. Requested/default duration, PCM16/mono, target LUFS/peak and inferred clipping
   count could masquerade as measurement. These fallbacks are removed from QC.
5. Fade success used audio overall status. Individual fade statuses now come
   from their own backend flags. Structured explicit FAIL wins over legacy PASS.
6. The Studio header always claimed VALIDATED. It now shows the backend Module 8
   status, REFRESHING, NO JOB or UNAVAILABLE as appropriate.
7. Failed refreshes retained old PASS and hid refresh errors. Refresh invalidates
   old evidence and displays errors without fallback to job/manifest results.
8. Stem-list failure blocked readable QC results. These requests are independent.
9. Operation completion mixed child cached results into parent state. Parent
   jobs and fresh QC are reloaded after regeneration/ducking/looping. Same-job
   updates trigger refresh; obsolete responses cannot overwrite newer jobs.
10. Required outputs, failure reasons and complete evidence were inaccessible.
    They are displayed, with a working report button. Disabled operations are
    NOT APPLICABLE rather than assumed successes or nonexistent media players.

## Automated verification

- `npm test -- --reporter=dot`: **30 passed**, 2 test files (23 new + 7 existing).
- `npx tsc --noEmit`: **PASS**.
- `npm run build`: **PASS**.
- Editor diagnostics: no errors in changed TypeScript files.

Tests include all requested A–G cases and explicit no-default/no-cache behavior.
The looping and ducking flow tests use the actual App flow and assert a parent
job refetch followed by fresh measured Module 8 FAIL rendering, despite child
PASS metadata.

## Browser verification

Used the running app at http://localhost:5173 with controlled browser-session
HTTP responses. The normal Create → generation progress → Open Studio path was
used; the real exported App/Studio components rendered the responses. Neither
source files nor backend data were changed by browser mocks.

| Case | Controlled response | Observed browser result |
| --- | --- | --- |
| A | Overall FAIL; stems false; actual 3, expected 4, missing pads; four legacy entries | Stem validation **FAIL**, actual 3/expected 4, missing pads, alignment No |
| B | Persisted loop PASS; fresh loop false; discontinuity 0.8 | Loop **FAIL**, jump 0.8; no seamless-success playback label |
| C | measured -16.2, target -16.0 | **Measured -16.2 LUFS** / **Target -16.0 LUFS** |
| D | actual 44100, expected 48000, false; conflicting legacy PASS | **Measured 44100 Hz**, **Expected 48000 Hz**, **FAIL** |
| E | clipping false; 25 clipped samples; legacy PASS | **25 clipped samples**, **FAIL** |
| F | reconstruction false, error -12 | **Measured -12 dB**, **FAIL** |
| G | Fresh overall PASS and passing checks | Header **Module 8 PASS**, all returned checks PASS |
| Ducking | attenuation 3.4, recovery false, speech 2.2–3.8s | **FAIL**, measured 3.4dB, recovery No, actual supplied speech interval |
| Outputs | missing pads.wav, unreadable piano.wav | Output **FAIL** and supplied file/reason details |
| Refresh error | HTTP 503 following displayed PASS | **UNAVAILABLE**, error visible, previous PASS/verification/loop evidence removed |
| Empty Studio | Reload removes mocks; no active job | NO JOB, unavailable measurements; no VALIDATED/ACTIVE/backend-verified claim |
| Report action | Fresh FAIL evidence loaded | Dialog contains the actual supplied failed backend checks |

Browser assertions explicitly required each failed QC badge to equal FAIL and
confirmed the loop card contained no PASS despite persisted PASS metadata.
The FAIL and PASS scenarios were repeated after the final test/build run.
Browser-only mocks were removed by reload after verification.

Mock job IDs deliberately have no real WAVs, so media players reported missing
mock audio. This is expected and separate from the measured-response UI proof;
this task did not rerun backend WAV compliance or audio playback verification.

## Result and limits

**Module 8 frontend: compliant for the requested measured-evidence display,
status mapping and refresh cases. Backend FAIL is displayed as FAIL.**

No backend changes were necessary: the aggregate Module 8 response already
provides measured ducking and loop evidence. Missing fields are explicitly
unavailable, never substituted with fabricated measurements or success.
Unrelated sample Test Lab/Projects/Settings screens remain outside this scoped
change; they are not the source of Studio Module 8 decisions.