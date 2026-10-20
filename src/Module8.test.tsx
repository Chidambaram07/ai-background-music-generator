import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MusicJob, MusicValidationResponse } from "./services/musicApi";

const api = vi.hoisted(() => ({
  getMusicValidation: vi.fn(), getLoopValidation: vi.fn(), getMusicStems: vi.fn(),
  regenerateStem: vi.fn(), waitForMusicJob: vi.fn(), getMusicJob: vi.fn(),
  createLoopJob: vi.fn(), createVoiceoverDuckingJob: vi.fn(), generateMusicFromSpecification: vi.fn(),
  uploadVoiceoverFile: vi.fn(), createMusicSpecification: vi.fn(),
}));
vi.mock("./services/musicApi", async () => ({
  ...await vi.importActual<typeof import("./services/musicApi")>("./services/musicApi"), ...api,
}));
vi.mock("./services/specificationApi", async () => ({
  ...await vi.importActual<typeof import("./services/specificationApi")>("./services/specificationApi"),
  createMusicSpecification: api.createMusicSpecification,
}));
import App, { Studio } from "./App";

const ids = ["drums", "bass", "piano", "pads"];
function job(id = "qc-job"): MusicJob {
  return { job_id: id, status: "succeeded", progress: 100, created_at: "2026-10-19T00:00:00Z",
    updated_at: "2026-10-19T00:00:00Z", prompt: "QC test", duration_s: 45, error: null,
    result: { stems: ids.map(stem_id => ({ stem_id, name: stem_id })), loop: { status: "PASS", loop_start_s: 5, loop_end_s: 17 } } };
}
function response(id = "qc-job"): MusicValidationResponse {
  return { job_id: id, overall_status: "PASS", valid: true,
    // Deliberately conflicting legacy statuses must not defeat structured FAIL.
    audio_validation: { overall_status: "PASS", sample_rate_status: "PASS", clipping_status: "PASS", format_status: "PASS", loudness_status: "PASS" },
    stems: Object.fromEntries(ids.map(name => [name, { exists: true, pass: true }])),
    checks: {
      duration: { actual_s: 44.99, requested_s: 45, error_s: 0.01, tolerance_s: 0.05, pass: true },
      sample_rate: { actual_hz: 48000, expected_hz: 48000, pass: true },
      format: { container: "WAV", encoding: "PCM", subtype: "PCM_16", bit_depth: 16, channels: 1, pass: true },
      loudness: { measured_lufs: -16.2, target_lufs: -16.0, tolerance_lufs: 1, pass: true },
      true_peak: { measured_dbtp: -3.2, limit_dbtp: -1, pass: true },
      clipping: { clipped_samples: 0, threshold: 0.9999, pass: true },
      fades: { fade_in_ms: 9.994, fade_out_ms: 9.989, expected_fade_in_ms: 10, expected_fade_out_ms: 10, fade_in_pass: true, fade_out_pass: true, pass: true },
      stems: { actual_count: 4, expected_count: 4, missing_stems: [], extra_stems: [], sample_alignment: { aligned: true }, pass: true,
        details: { piano: { duration_s: 44.99, exists: true, readable: true, sample_rate_hz: 48000, pass: true } } },
      stem_reconstruction: { stem_reconstruction_error_db: -75, rms_error: 0.0001, pass: true },
      ducking: { enabled: false, pass: true }, looping: { enabled: false, pass: true },
      required_outputs: { present_files: ["mix.wav"], missing_files: [], unreadable_files: {}, pass: true },
    } };
}
const qcRow = (name: string) => {
  const element = document.querySelector(`[data-qc-check="${name}"]`);
  if (!element) throw new Error(`Missing QC row ${name}`);
  return within(element as HTMLElement);
};
async function show(data = response(), active = job()) {
  api.getMusicValidation.mockResolvedValue(data);
  const view = render(<Studio job={active} />);
  await waitFor(() => expect(document.querySelector('[data-qc-check="Module 8"]')).not.toBeNull());
  return view;
}

describe("Module 8 measured frontend evidence", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    api.getMusicStems.mockImplementation(async (id: string) => ({ job_id: id, stems: ids.map(stem_id => ({ stem_id, name: stem_id, url: `/stems/${stem_id}` })), mix_url: "/mix" }));
    api.getLoopValidation.mockResolvedValue({ job_id: "qc-job", loop: { enabled: false, pass: true } });
  });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("A: preserves stem FAIL despite four legacy metadata entries", async () => {
    const data = response(); data.overall_status = "FAIL"; data.valid = false;
    data.checks!.stems = { pass: false, actual_count: 3, expected_count: 4, missing_stems: ["pads"], extra_stems: ["unexpected"], sample_alignment: { aligned: false },
      details: { pads: { exists: false, readable: false, pass: false }, piano: { exists: true, readable: false, pass: false, reason: "Unreadable PCM" } } };
    await show(data);
    expect(qcRow("Stem validation").getByText("FAIL")).toBeInTheDocument();
    expect(qcRow("Stem validation").getByText("Actual 3 stems · Expected 4")).toBeInTheDocument();
    expect(qcRow("Stem validation").getByText(/Missing: pads · Extra: unexpected · Aligned: No/)).toBeInTheDocument();
    expect(screen.getByText("Missing file")).toBeInTheDocument();
    expect(screen.getByText("Unreadable file")).toBeInTheDocument();
    expect(screen.queryByText("VALIDATED")).not.toBeInTheDocument();
  });

  it("B: fresh loop FAIL overrides persisted PASS", async () => {
    const data = response(); delete data.checks!.looping;
    api.getLoopValidation.mockResolvedValue({ job_id: "qc-job", loop: { enabled: true, pass: false, boundary_discontinuity: 0.8, loop_start_s: 40, loop_end_s: 42.666667, loop_duration_s: 2.666667 } });
    await show(data);
    expect(qcRow("Loop validation").getByText("FAIL")).toBeInTheDocument();
    const card = within(document.querySelector(".loop-card") as HTMLElement);
    expect(card.getAllByText("FAIL")).toHaveLength(2);
    expect(card.getByText("0.8")).toBeInTheDocument();
    expect(card.queryByText("PASS")).not.toBeInTheDocument();
    expect(screen.queryByText("Seamless looping active")).not.toBeInTheDocument();
  });

  it("C: distinguishes measured -16.2 LUFS from target -16.0 LUFS", async () => {
    await show();
    expect(qcRow("Loudness").getByText("Measured -16.2 LUFS")).toBeInTheDocument();
    expect(qcRow("Loudness").getByText(/Target -16.0 LUFS/)).toBeInTheDocument();
  });

  it("D: shows actual 44100 Hz, expected 48000 Hz, and backend FAIL", async () => {
    const data = response(); data.checks!.sample_rate = { actual_hz: 44100, expected_hz: 48000, pass: false };
    await show(data);
    expect(qcRow("Sample Rate").getByText("Measured 44100 Hz")).toBeInTheDocument();
    expect(qcRow("Sample Rate").getByText("Expected 48000 Hz")).toBeInTheDocument();
    expect(qcRow("Sample Rate").getByText("FAIL")).toBeInTheDocument();
  });

  it("E: shows 25 clipped samples and FAIL despite legacy PASS", async () => {
    const data = response(); data.checks!.clipping = { pass: false, clipped_samples: 25 };
    await show(data);
    expect(qcRow("Clipping").getByText("25 clipped samples")).toBeInTheDocument();
    expect(qcRow("Clipping").getByText("FAIL")).toBeInTheDocument();
  });

  it("F: reconstruction -12 dB stays FAIL", async () => {
    const data = response(); data.checks!.stem_reconstruction = { pass: false, stem_reconstruction_error_db: -12 };
    await show(data);
    expect(qcRow("Stem Reconstruction").getByText("Measured -12 dB")).toBeInTheDocument();
    expect(qcRow("Stem Reconstruction").getByText("FAIL")).toBeInTheDocument();
  });

  it("G: displays the backend Module 8 PASS without recalculating it", async () => {
    await show();
    expect(screen.getByText("Module 8 PASS")).toBeInTheDocument();
    expect(qcRow("Module 8").getAllByText("PASS")).toHaveLength(2);
    expect(api.getLoopValidation).not.toHaveBeenCalled();
  });

  it("uses only fresh Module 8 loop checks when available", async () => {
    const data = response(); data.checks!.looping = { enabled: true, pass: false, boundary_discontinuity: 0.8 };
    await show(data);
    expect(qcRow("Loop validation").getByText("FAIL")).toBeInTheDocument();
    expect(api.getLoopValidation).not.toHaveBeenCalled();
  });

  it("fades use their own results, not audio overall status", async () => {
    const data = response(); data.checks!.fades.fade_out_pass = false; data.checks!.fades.pass = false;
    await show(data);
    expect(qcRow("Fade-in").getByText("PASS")).toBeInTheDocument();
    expect(qcRow("Fade-out").getByText("FAIL")).toBeInTheDocument();
    expect(qcRow("Fades").getByText("FAIL")).toBeInTheDocument();
  });

  it("displays measured failed ducking rather than the requested attenuation", async () => {
    const data = response(); data.checks!.ducking = { enabled: true, pass: false, ducking_db: 3.4, recovers_after_speech: false,
      speech_regions: [{ start_s: 2.2, end_s: 3.8 }], transitions_smooth: false };
    await show(data);
    expect(qcRow("Ducking").getByText("FAIL")).toBeInTheDocument();
    const card = within(document.querySelector(".voiceover-panel") as HTMLElement);
    expect(card.getByText("3.4 dB")).toBeInTheDocument();
    expect(card.getByText("8–15 dB")).toBeInTheDocument();
    expect(card.getByText("2.2 sec–3.8 sec")).toBeInTheDocument();
    expect(card.queryByText(/ACTIVE/)).not.toBeInTheDocument();
  });

  it("disabled operations are not shown as active or offered nonexistent audio", async () => {
    await show();
    expect(screen.queryByText("DUCKED MIX")).not.toBeInTheDocument();
    expect(screen.queryByText("LOOPED OUTPUT")).not.toBeInTheDocument();
    expect(qcRow("Ducking").getByText("NOT APPLICABLE")).toBeInTheDocument();
    expect(qcRow("Loop validation").getByText("NOT APPLICABLE")).toBeInTheDocument();
  });

  it("missing measurements never become requested duration or format defaults", async () => {
    await show({ job_id: "qc-job", overall_status: "FAIL", checks: {}, audio_validation: {} });
    expect(qcRow("Duration").getByText("Measured Unavailable")).toBeInTheDocument();
    expect(qcRow("Format").getByText("Unavailable · Unavailable")).toBeInTheDocument();
    expect(qcRow("Clipping").getByText("Unavailable clipped samples")).toBeInTheDocument();
    expect(screen.queryByText("Target -16.0 LUFS")).not.toBeInTheDocument();
  });

  it("nested validation_result remains usable without legacy audio fields", async () => {
    const base = response();
    await show({ job_id: "qc-job", validation_result: { overall_status: "FAIL", valid: false, checks: { ...base.checks, stems: { actual_count: 3, expected_count: 4, pass: false } } } });
    expect(qcRow("Stem validation").getByText("FAIL")).toBeInTheDocument();
    expect(screen.getByText("Module 8 FAIL")).toBeInTheDocument();
  });

  it("does not hide measured failures when stem listing fails", async () => {
    api.getMusicStems.mockRejectedValue(new Error("Missing stem listing"));
    const data = response(); data.overall_status = "FAIL"; data.valid = false;
    await show(data);
    expect(screen.getByText("Module 8 FAIL")).toBeInTheDocument();
  });

  it("required missing/unreadable output evidence is visible", async () => {
    const data = response(); data.checks!.required_outputs = { pass: false, present_files: [], missing_files: ["pads.wav"], unreadable_files: { "mix.wav": "Invalid header" } };
    await show(data);
    expect(qcRow("Required outputs").getByText(/pads.wav.*mix.wav: Invalid header/)).toBeInTheDocument();
    expect(qcRow("Required outputs").getByText("FAIL")).toBeInTheDocument();
  });

  it("refresh failure removes stale PASS instead of falling back to cached metadata", async () => {
    await show();
    api.getMusicValidation.mockRejectedValueOnce(new Error("QC service unavailable"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh measured QC" }));
    await waitFor(() => expect(screen.getByText("QC service unavailable")).toBeInTheDocument());
    expect(screen.queryByText("Module 8 PASS")).not.toBeInTheDocument();
    expect(screen.queryByText("Backend measured evidence")).not.toBeInTheDocument();
  });

  it("refreshes when same-job result/operation revision changes", async () => {
    const active = job(); const view = await show(response(), active);
    const failed = response(); failed.valid = false; failed.overall_status = "FAIL";
    api.getMusicValidation.mockResolvedValue(failed);
    view.rerender(<Studio job={{ ...active, updated_at: "2026-10-19T00:05:00Z", result: { ...active.result, ducking: { ducking_status: "PASS" } } }} />);
    await waitFor(() => expect(screen.getByText("Module 8 FAIL")).toBeInTheDocument());
    expect(api.getMusicValidation).toHaveBeenCalledTimes(2);
  });

  it("discards an older job's delayed PASS response", async () => {
    let resolveOld!: (data: MusicValidationResponse) => void;
    api.getMusicValidation.mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve; }));
    const view = render(<Studio job={job("old-job")} />);
    const failed = response("new-job"); failed.overall_status = "FAIL"; failed.valid = false;
    api.getMusicValidation.mockResolvedValueOnce(failed);
    view.rerender(<Studio job={job("new-job")} />);
    await waitFor(() => expect(screen.getByText("Module 8 FAIL")).toBeInTheDocument());
    await act(async () => resolveOld(response("old-job")));
    expect(screen.queryByText("Module 8 PASS")).not.toBeInTheDocument();
  });

  it("regeneration invalidates QC and fetches the revalidated parent", async () => {
    const onJobRefresh = vi.fn();
    api.getMusicValidation.mockResolvedValue(response());
    api.regenerateStem.mockResolvedValue({ job_id: "regen" });
    api.waitForMusicJob.mockResolvedValue({ status: "succeeded" });
    api.getMusicJob.mockResolvedValue({ ...job(), updated_at: "2026-10-19T01:00:00Z" });
    render(<Studio job={job()} onJobRefresh={onJobRefresh} />);
    await waitFor(() => expect(screen.getByText("Module 8 PASS")).toBeInTheDocument());
    const failed = response(); failed.valid = false; failed.overall_status = "FAIL";
    api.getMusicValidation.mockResolvedValue(failed);
    fireEvent.click(screen.getAllByRole("button", { name: "↻ Regenerate" })[1]);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate Stem" }));
    await waitFor(() => expect(screen.getByText("Module 8 FAIL")).toBeInTheDocument());
    expect(onJobRefresh).toHaveBeenCalledWith(expect.objectContaining({ job_id: "qc-job" }));
  });

  it("report action displays the fresh full backend response", async () => {
    await show();
    fireEvent.click(screen.getByRole("button", { name: "View validation report →" }));
    expect(screen.getByRole("dialog", { name: "Module 8 measured validation report" })).toHaveTextContent("stem_reconstruction");
  });

  it.each(["looping", "ducking"])("refetches parent and measured QC after %s, not child PASS metadata", async operation => {
    api.createMusicSpecification.mockImplementation(async specification => ({ specification }));
    api.generateMusicFromSpecification.mockResolvedValue({ job_id: "qc-job", status: "queued" });
    api.createLoopJob.mockResolvedValue({ job_id: "operation-job", status: "queued" });
    api.createVoiceoverDuckingJob.mockResolvedValue({ job_id: "operation-job", status: "queued" });
    api.uploadVoiceoverFile.mockResolvedValue({ filename: "voiceover.wav", path: "/voiceover.wav" });
    api.waitForMusicJob.mockImplementation(async (id, onUpdate) => {
      const next = id === "qc-job" ? job() : { ...job("operation-job"), parent_job_id: "qc-job", result: { loop: { status: "PASS" }, ducking: { ducking_status: "PASS" } } };
      onUpdate(next);
      return next;
    });
    api.getMusicJob.mockResolvedValue({ ...job(), updated_at: "2026-10-19T01:00:00Z" });
    const failed = response(); failed.overall_status = "FAIL"; failed.valid = false;
    api.getMusicValidation.mockResolvedValue(failed);
    const view = render(<App />);
    const heading = screen.getByText(operation === "looping" ? "Seamless Looping" : "Voiceover Ducking");
    fireEvent.click(within(heading.closest(".optional-card") as HTMLElement).getByRole("button", { name: "Turn on" }));
    if (operation === "ducking") {
      const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
      fireEvent.change(input, { target: { files: [new File(["voice"], "voiceover.wav", { type: "audio/wav" })] } });
      await waitFor(() => expect(api.uploadVoiceoverFile).toHaveBeenCalled());
      await waitFor(() => expect(screen.getByText("Uploaded voiceover")).toBeInTheDocument());
    }
    fireEvent.click(screen.getByRole("button", { name: /Generate Background Music/ }));
    await waitFor(() => expect(api.getMusicJob).toHaveBeenCalledWith("qc-job"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Open Studio" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Open Studio" }));
    await waitFor(() => expect(screen.getByText("Module 8 FAIL")).toBeInTheDocument());
    expect(api.getMusicValidation).toHaveBeenCalledWith("qc-job");
  });

  it("without a job there are no verification or active-operation claims", () => {
    render(<Studio job={null} />);
    expect(screen.queryByText("VALIDATED")).not.toBeInTheDocument();
    expect(screen.queryByText(/ACTIVE/)).not.toBeInTheDocument();
    expect(screen.queryByText("Backend measured evidence")).not.toBeInTheDocument();
    expect(api.getMusicValidation).not.toHaveBeenCalled();
  });
});