const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  status: number;
  code: string;
  retryable: boolean;
  details: unknown;

  constructor(status: number, code: string, message: string, retryable = false, details: unknown = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
    this.details = details;
  }
}

export async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 15000);
  try {
    const headers = new Headers(options.headers);
    headers.set("Content-Type", "application/json");
    if (options.method?.toUpperCase() === "POST" && !headers.has("Idempotency-Key")) {
      headers.set("Idempotency-Key", crypto.randomUUID());
    }
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      signal: controller.signal,
      headers,
    });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const error = data?.error;
      throw new ApiError(
        response.status,
        error?.code || "HTTP_ERROR",
        error?.message || "The service returned an unexpected error.",
        Boolean(error?.retryable),
        error?.details || {},
      );
    }
    return data as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError(408, "REQUEST_TIMEOUT", "The music service took too long to respond.", true);
    }
    throw new ApiError(0, "NETWORK_ERROR", "Unable to connect to the music generation service. Please try again.", true);
  } finally {
    window.clearTimeout(timeout);
  }
}

export { API_BASE_URL };
