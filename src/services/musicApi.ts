import { API_BASE_URL, ApiError, apiRequest } from "./api";

export type JobStatus =
  | "queued"
  | "processing"
  | "succeeded"
  | "failed"
  | "cancelled";

export type MusicJob = {
  job_id: string;
  status: JobStatus;
  progress: number;
  created_at: string;
  updated_at: string;
  prompt: string;
  duration_s: number;
  stem_id?: string | null;
  parent_job_id?: string | null;
  error: {
    code: string;
    message: string;
    retryable: boolean;
    details: Record<string, unknown>;
  } | null;
  result:
    | (GenerationResult & Record<string, unknown>)
    | Record<string, unknown>
    | null;
};

type AcceptedJob = {
  job_id: string;
  status: "queued";
};

export type GeneratedAudio = {
  stem_id?: string;
  name: string;
  filename: string;
  url: string;
};

export type StemInfo = {
  stem_id: string;
  name: string;
  filename: string;
  url: string;
};

export type StemListResponse = {
  job_id: string;
  stems: StemInfo[];
  mix_url: string;
};



export type StemRegenerationAccepted = {
  job_id: string;
  stem_id: string;
  status: "queued" | "processing";
};

export type MusicValidationResponse = {
  job_id: string;
  overall_status?: "PASS" | "FAIL" | string;
  valid?: boolean;
  summary?: Record<string, unknown> | null;
  checks?: Record<string, Record<string, unknown>> | null;
  validation_result?: Record<string, unknown> | null;
  audio_validation?: Record<string, unknown>;
  stems?: Record<string, Record<string, unknown>>;
};

export type LoopValidationResponse = {
  job_id: string;
  loop: {
    [key: string]: unknown;
    pass?: boolean;
    enabled?: boolean;
    loop_start_s?: number;
    loop_end_s?: number;
    loop_duration_s?: number;
    bpm?: number;
    beats_per_bar?: number;
    start_aligned_to_bar?: boolean;
    end_aligned_to_bar?: boolean;
    boundary_discontinuity?: number;
    clipping_detected?: boolean;
    status?: string;
    sample_rate?: number | Record<string, unknown>;
    duration_s?: number;
    actual_duration_s?: number;
    clipping?: Record<string, unknown>;
  };
};

export type ValidationRequirement = {
  requirement_id: string;
  name: string;
  category: string;
  status: "PASS" | "WARNING" | "FAIL" | string;
  summary: string;
  evidence: Record<string, unknown>;
  associated_job_id: string | null;
};

export type Ps1ValidationReport = {
  job_id: string;
  overall_status: "PASS" | "WARNING" | "FAIL" | string;
  passed_count: number;
  warning_count: number;
  failed_count: number;
  percentage: number;
  validated_at: string;
  requirements: ValidationRequirement[];
};

export type GenerationResult = {
  duration_seconds: number;
  sample_rate: number;
  stems: GeneratedAudio[];
  mix: GeneratedAudio;
  validation: Record<string, unknown>;
  audio_validation?: Record<string, unknown>;
  stems_validation?: Record<string, Record<string, unknown>>;
  seed: number;
};

type CreateJobInput = {
  prompt: string;
  duration_s: number;
};

function newIdempotencyKey() {
  return (
    globalThis.crypto?.randomUUID?.() ||
    `${Date.now()}-${Math.random().toString(36).slice(2)}`
  );
}

export async function createMusicJob(
  input: CreateJobInput,
): Promise<AcceptedJob> {
  return apiRequest<AcceptedJob>("/api/v1/music/jobs", {
    method: "POST",
    headers: {
      "Idempotency-Key": newIdempotencyKey(),
    },
    body: JSON.stringify(input),
  });
}

export function generateMusicFromSpecification(
  specification: object,
): Promise<AcceptedJob> {
  return apiRequest<AcceptedJob>("/api/v1/music/generate", {
    method: "POST",
    headers: {
      "Idempotency-Key": newIdempotencyKey(),
    },
    body: JSON.stringify({ specification }),
  });
}

export function audioUrl(
  jobId: string,
  name: string,
  version?: string,
): string {
  const safeName = encodeURIComponent(name);

  const base = `${
    import.meta.env.VITE_API_BASE_URL || "http://localhost:8000"
  }`.replace(/\/$/, "");

  const path = `${base}/api/v1/music/jobs/${encodeURIComponent(jobId)}/${
    name === "mix" ? "mix" : `stems/${safeName}`
  }`;

  console.log("[musicApi] audioUrl", { jobId, name, resolvedPath: path });

  return version
    ? `${path}?v=${encodeURIComponent(version)}`
    : path;
}

export function duckedAudioUrl(jobId: string, version?: string): string {
  const base = `${
    import.meta.env.VITE_API_BASE_URL || "http://localhost:8000"
  }`.replace(/\/$/, "");
  const path = `${base}/api/v1/music/jobs/${encodeURIComponent(jobId)}/duck`;
  console.log("[musicApi] duckedAudioUrl", { jobId, resolvedPath: path });
  return version ? `${path}?v=${encodeURIComponent(version)}` : path;
}

export function loopAudioUrl(jobId: string, version?: string): string {
  const base = `${
    import.meta.env.VITE_API_BASE_URL || "http://localhost:8000"
  }`.replace(/\/$/, "");
  const path = `${base}/api/v1/music/jobs/${encodeURIComponent(jobId)}/loop`;
  console.log("[musicApi] loopAudioUrl", { jobId, resolvedPath: path });
  return version ? `${path}?v=${encodeURIComponent(version)}` : path;
}

export function getMusicStems(
  jobId: string,
): Promise<StemListResponse> {
  return apiRequest<StemListResponse>(
    `/api/v1/music/jobs/${encodeURIComponent(jobId)}/stems`,
  );
}

/**
 * Regenerate exactly one stem.
 *
 * IMPORTANT:
 * The backend must accept the JSON body:
 *
 * {
 *   instruction: string,
 *   preserve: {
 *     tempoAndKey: boolean,
 *     timelineBoundaries: boolean,
 *     overallMood: boolean,
 *     masterDuration: boolean
 *   }
 * }
 */
export type StemRegenerationPreserve = {
  tempoAndKey: boolean;
  timelineBoundaries: boolean;
  overallMood: boolean;
  masterDuration: boolean;
};

export type StemRegenerationOptions = {
  instruction: string;
  preserve: StemRegenerationPreserve;
};

export type StemRegenerationRequest = {
  instruction: string;
  preserve: {
    tempoAndKey: boolean;
    timelineBoundaries: boolean;
    overallMood: boolean;
    masterDuration: boolean;
  };
};

export function regenerateStem(
  jobId: string,
  stemId: string,
  request: StemRegenerationRequest
): Promise<StemRegenerationAccepted> {
  return apiRequest<StemRegenerationAccepted>(
    `/api/v1/music/jobs/${encodeURIComponent(jobId)}/stems/${encodeURIComponent(stemId)}/regenerate`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": newIdempotencyKey(),
      },
      body: JSON.stringify(request),
    }
  );
}

export function getMusicJob(
  jobId: string,
): Promise<MusicJob> {
  return apiRequest<MusicJob>(
    `/api/v1/jobs/${encodeURIComponent(jobId)}`,
  );
}

export function cancelMusicJob(jobId: string) {
  return apiRequest<{
    job_id: string;
    status: "cancelled";
  }>(
    `/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`,
    {
      method: "POST",
    },
  );
}

export async function waitForMusicJob(
  jobId: string,
  onUpdate: (job: MusicJob) => void,
  intervalMs = 500,
): Promise<MusicJob> {
  while (true) {
    const job = await getMusicJob(jobId);

    onUpdate(job);

    if (
      job.status === "succeeded" ||
      job.status === "failed" ||
      job.status === "cancelled"
    ) {
      return job;
    }

    await new Promise((resolve) =>
      window.setTimeout(resolve, intervalMs),
    );
  }
}

export type UploadVoiceoverResponse = {
  filename: string;
  path: string;
  size_bytes: number;
  url: string;
};

export async function uploadVoiceoverFile(
  file: File,
): Promise<UploadVoiceoverResponse> {
  const formData = new FormData();
  formData.append("file", file, file.name);

  const response = await fetch(`${API_BASE_URL}/api/v1/music/upload`, {
    method: "POST",
    headers: { "Idempotency-Key": newIdempotencyKey() },
    body: formData,
  });

  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const error = data?.error;
    throw new ApiError(
      response.status,
      error?.code || "UPLOAD_ERROR",
      error?.message || "Unable to upload the voiceover file.",
      Boolean(error?.retryable),
      error?.details || {},
    );
  }

  return data as UploadVoiceoverResponse;
}

export async function createVoiceoverDuckingJob(
  jobId: string,
  voiceoverPath: string,
  duckDb = 12.0,
  attackMs = 50.0,
  releaseMs = 300.0,
): Promise<AcceptedJob> {
  return apiRequest<AcceptedJob>(`/api/v1/music/jobs/${encodeURIComponent(jobId)}/duck`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": newIdempotencyKey(),
    },
    body: JSON.stringify({
      voiceover_path: voiceoverPath,
      duck_db: duckDb,
      attack_ms: attackMs,
      release_ms: releaseMs,
    }),
  });
}

export function createLoopJob(
  jobId: string,
  bpm?: number,
  beatsPerBar?: number,
): Promise<AcceptedJob> {
  return apiRequest<AcceptedJob>(`/api/v1/music/jobs/${encodeURIComponent(jobId)}/loop`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": newIdempotencyKey(),
    },
    body: JSON.stringify({
      bpm: bpm ?? undefined,
      beats_per_bar: beatsPerBar ?? undefined,
    }),
  });
}

export function getLoopValidation(
  jobId: string,
): Promise<LoopValidationResponse> {
  return apiRequest<LoopValidationResponse>(
    `/api/v1/music/jobs/${encodeURIComponent(jobId)}/loop/validation`,
    { cache: "no-store" },
  );
}

export function getMusicValidation(
  jobId: string,
): Promise<MusicValidationResponse> {
  return apiRequest<MusicValidationResponse>(
    `/api/v1/music/jobs/${encodeURIComponent(jobId)}/validation`,
    { cache: "no-store" },
  );
}

export function getPs1Validation(
  jobId: string,
): Promise<Ps1ValidationReport> {
  return apiRequest<Ps1ValidationReport>(
    `/api/v1/music/jobs/${encodeURIComponent(jobId)}/ps1-validation`,
    { cache: "no-store" },
  );
}

export function runPs1Validation(
  jobId: string,
): Promise<Ps1ValidationReport> {
  return apiRequest<Ps1ValidationReport>(
    `/api/v1/music/jobs/${encodeURIComponent(jobId)}/ps1-validation`,
    {
      method: "POST",
    },
  );
}

export function ps1ValidationReportUrl(jobId: string): string {
  const base = `${
    import.meta.env.VITE_API_BASE_URL || "http://localhost:8000"
  }`.replace(/\/$/, "");
  return `${base}/api/v1/music/jobs/${encodeURIComponent(jobId)}/ps1-validation/report`;
}

function packageDownloadUrl(jobId: string): string {
  const base = `${
    import.meta.env.VITE_API_BASE_URL || "http://localhost:8000"
  }`.replace(/\/$/, "");
  return `${base}/api/v1/jobs/${encodeURIComponent(jobId)}/download/package`;
}

function responseFilename(response: Response, fallback: string): string {
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename\*?=(?:UTF-8''|\")?([^\";]+)/i);
  if (!match?.[1]) return fallback;
  return decodeURIComponent(match[1].replace(/\"/g, "").trim());
}

export async function downloadJobPackage(jobId: string): Promise<string> {
  const response = await fetch(packageDownloadUrl(jobId), {
    method: "GET",
  });

  if (!response.ok) {
    const data = await response.json().catch(() => null);
    const error = data?.error;
    throw new ApiError(
      response.status,
      error?.code || "DOWNLOAD_ERROR",
      error?.message || "Unable to download the package.",
      Boolean(error?.retryable),
      error?.details || {},
    );
  }

  const filename = responseFilename(response, `incubrix_${jobId}_package.zip`);
  const blob = await response.blob();
  const objectUrl = window.URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => window.URL.revokeObjectURL(objectUrl), 0);
  return filename;
}