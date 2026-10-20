import type { MusicValidationResponse } from "./musicApi";

export type Evidence = Record<string, unknown>;
export type QcStatus = "PASS" | "FAIL" | "UNAVAILABLE" | "NOT APPLICABLE";
export const record = (value: unknown): Evidence => value && typeof value === "object" && !Array.isArray(value) ? value as Evidence : {};
export const numeric = (value: unknown): number | undefined => typeof value === "number" && Number.isFinite(value) ? value : undefined;
export const measured = (value: unknown, unit = "", decimals?: number) => {
  const number = numeric(value);
  return number === undefined ? "Unavailable" : `${decimals === undefined ? number : number.toFixed(decimals)}${unit ? ` ${unit}` : ""}`;
};
export const text = (value: unknown) => typeof value === "string" && value.length ? value : "Unavailable";
export const list = (value: unknown) => Array.isArray(value) ? value.map(String).join(", ") || "None" : "Unavailable";
export const yesNo = (value: unknown) => value === true ? "Yes" : value === false ? "No" : "Unavailable";

// Status is a backend decision, never a count, threshold or metadata heuristic.
export function evidenceStatus(check: Evidence, legacyStatus?: unknown): QcStatus {
  if (check.pass === false) return "FAIL";
  if (check.enabled === false) return "NOT APPLICABLE";
  if (check.pass === true) return "PASS";
  const status = check.status ?? legacyStatus;
  return status === "PASS" || status === "FAIL" ? status : "UNAVAILABLE";
}

export function validationEvidence(response: MusicValidationResponse | null) {
  const nested = record(response?.validation_result);
  const checks = { ...record(nested.checks), ...record(response?.checks) };
  const av = { ...record(nested.audio_validation), ...record(response?.audio_validation) };
  const status = response?.valid === false || nested.valid === false ? "FAIL"
    : evidenceStatus({ status: response?.overall_status ?? nested.overall_status,
        ...(response?.overall_status || nested.overall_status ? {} : { pass: response?.valid ?? nested.valid }) });
  return { checks, av, status, check: (name: string) => record(checks[name]) };
}

export type QcRow = { name: string; value: string; note: string; status: QcStatus; reason?: string };
export function qualityRows(response: MusicValidationResponse): QcRow[] {
  const { checks, av, check, status } = validationEvidence(response);
  const d = check("duration"), sr = check("sample_rate"), f = check("format"), l = check("loudness");
  const p = check("true_peak"), c = check("clipping"), fade = check("fades"), s = check("stems");
  const reconstruction = check("stem_reconstruction"), duck = check("ducking"), loop = check("looping"), outputs = check("required_outputs");
  const row = (name: string, value: string, note: string, evidence: Evidence, legacy?: unknown): QcRow => ({
    name, value, note, status: evidenceStatus(evidence, legacy), reason: typeof evidence.reason === "string" ? evidence.reason : undefined,
  });
  const rows = [
    row("Duration", `Measured ${measured(d.actual_s ?? av.actual_duration_seconds, "sec", 3)}`,
      `Requested ${measured(d.requested_s ?? av.requested_duration_seconds, "sec", 3)} · Error ${measured(d.error_s ?? av.duration_error_seconds, "sec", 3)} · Tolerance ${measured(d.tolerance_s ?? av.duration_tolerance_seconds, "sec")}`, d, av.duration_status),
    row("Sample Rate", `Measured ${measured(sr.actual_hz ?? sr.measured_sample_rate ?? av.sample_rate, "Hz")}`,
      `Expected ${measured(sr.expected_hz ?? sr.expected_sample_rate, "Hz")}`, sr, av.sample_rate_status),
    row("Format", `${text(f.container ?? av.format)} · ${text(f.subtype ?? av.subtype)}`,
      `Encoding ${text(f.encoding)} · ${measured(f.bit_depth, "bit")} · ${measured(f.channels ?? av.channels, "channels")}`, f, av.format_status),
    row("Loudness", `Measured ${measured(l.measured_lufs ?? av.measured_lufs, "LUFS", 1)}`,
      `Target ${measured(l.target_lufs ?? av.target_lufs, "LUFS", 1)} · Tolerance ${measured(l.tolerance_lufs ?? av.loudness_tolerance_lufs, "LU")}`, l, av.loudness_status),
    row("True Peak", `Measured ${measured(p.measured_dbtp ?? av.true_peak_dbTP, "dBTP", 3)}`,
      `Limit ${measured(p.limit_dbtp ?? av.true_peak_limit_dbTP, "dBTP")}`, p, av.true_peak_status),
    row("Clipping", `${measured(c.clipped_samples ?? av.clipped_samples)} clipped samples`,
      `Detected ${yesNo(c.clipping_detected ?? (numeric(c.clipped_samples) !== undefined ? Number(c.clipped_samples) > 0 : av.clipping_detected))} · Threshold ${measured(c.threshold)}`, c, av.clipping_status),
    row("Fade-in", `Measured ${measured(fade.fade_in_ms ?? av.fade_in_ms, "ms", 3)}`,
      `Expected ${measured(fade.expected_fade_in_ms, "ms")} · Method ${text(fade.measurement_method)}`, { pass: fade.fade_in_pass }),
    row("Fade-out", `Measured ${measured(fade.fade_out_ms ?? av.fade_out_ms, "ms", 3)}`,
      `Expected ${measured(fade.expected_fade_out_ms, "ms")} · Tolerance ${measured(fade.tolerance_ms, "ms")}`, { pass: fade.fade_out_pass }),
    row("Fades", "Waveform boundary validation", `Fit errors ${measured(fade.fade_in_score)} / ${measured(fade.fade_out_score)}`, fade),
    row("Stem validation", `Actual ${measured(s.actual_count)} stems · Expected ${measured(s.expected_count)}`,
      `Missing: ${list(s.missing_stems)} · Extra: ${list(s.extra_stems)} · Aligned: ${yesNo(record(s.sample_alignment).aligned)}`, s),
    row("Stem alignment", `Aligned ${yesNo(record(s.sample_alignment).aligned)}`,
      `Sample counts ${list(record(s.sample_alignment).sample_counts)} · Mix samples ${measured(record(s.sample_alignment).mix_sample_count)} · Rates ${list(record(s.sample_alignment).sample_rates)}`, { pass: record(s.sample_alignment).aligned }),
    row("Stem Reconstruction", `Measured ${measured(reconstruction.stem_reconstruction_error_db, "dB")}`,
      `RMS error ${measured(reconstruction.rms_error)}`, reconstruction),
    row("Ducking", `Measured attenuation ${measured(duck.ducking_db, "dB")}`,
      `Requirement target range 8–15 dB · Recovery ${yesNo(duck.recovers_after_speech)}`, duck),
    row("Loop validation", `Measured jump ${measured(loop.boundary_discontinuity)}`,
      `Enabled ${yesNo(loop.enabled)} · Interval ${measured(loop.loop_start_s, "sec")}–${measured(loop.loop_end_s, "sec")} · Duration ${measured(loop.loop_duration_s, "sec")}`, loop),
    row("Required outputs", `Present: ${list(outputs.present_files)}`,
      `Missing: ${list(outputs.missing_files)} · Unreadable: ${Object.entries(record(outputs.unreadable_files)).map(([file, reason]) => `${file}: ${String(reason)}`).join("; ") || (outputs.unreadable_files ? "None" : "Unavailable")}`, outputs),
    { name: "Module 8", value: status, note: "Backend overall validation", status },
  ];
  const known = new Set(["duration", "sample_rate", "format", "loudness", "true_peak", "clipping", "fades", "stems", "stem_reconstruction", "ducking", "looping", "required_outputs"]);
  for (const [name, evidence] of Object.entries(checks)) {
    if (!known.has(name)) rows.push(row(name, "Backend quality check", JSON.stringify(evidence), record(evidence)));
  }
  return rows;
}