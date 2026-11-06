export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: unknown,
  ) {
    super(message);
  }
}

const RETRYABLE_STATUSES = new Set([502, 503, 504]);
const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000];
const NETWORK_ERROR = "Can't reach the server. Check your connection and try again.";

interface RequestOptions {
  method?: string;
  body?: unknown;
  /**
   * Also retry when the server is waking. Reads always retry. Only set this for
   * writes that are safe to repeat.
   */
  retrySafe?: boolean;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function toApiError(response: Response): Promise<ApiError> {
  try {
    const body = await response.json();
    if (body && typeof body.message === "string") {
      return new ApiError(response.status, body.code, body.message, body.details);
    }
  } catch {
    // The hosting proxy can answer with HTML, so fall through to the generic message.
  }
  return new ApiError(
    response.status,
    "server_error",
    RETRYABLE_STATUSES.has(response.status)
      ? "The server is starting up. Please try again in a moment."
      : "Something went wrong on the server.",
  );
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const canRetry = method === "GET" || options.retrySafe === true;
  const attempts = canRetry ? RETRY_DELAYS_MS.length + 1 : 1;
  let lastError: ApiError | undefined;

  for (let attempt = 0; attempt < attempts; attempt++) {
    if (attempt > 0) {
      await sleep(RETRY_DELAYS_MS[attempt - 1]);
    }
    let response: Response;
    try {
      response = await fetch(`/api/v1${path}`, {
        method,
        credentials: "same-origin",
        headers: options.body === undefined ? undefined : { "Content-Type": "application/json" },
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
      });
    } catch {
      lastError = new ApiError(0, "network_error", NETWORK_ERROR);
      continue;
    }

    if (response.ok) {
      return (response.status === 204 ? undefined : await response.json()) as T;
    }

    const error = await toApiError(response);
    if (RETRYABLE_STATUSES.has(response.status)) {
      lastError = error;
      continue;
    }
    if (response.status === 401 && error.code === "not_authenticated") {
      const here = window.location.pathname + window.location.search;
      const next = here === "/" ? "" : `?next=${encodeURIComponent(here)}`;
      // This module has no router, and a full reload discards data from the old session.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign(`/login${next}`);
    }
    throw error;
  }
  throw lastError ?? new ApiError(0, "network_error", NETWORK_ERROR);
}

async function sha256Hex(file: File): Promise<string | null> {
  // Only available on HTTPS and localhost. The API's own hash still catches duplicates.
  if (!globalThis.crypto?.subtle) return null;
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

/**
 * Uploads with a checksum, so the API can refuse a file damaged on the way. Retrying is
 * safe: if a lost response hid a success, the retry reports it as a duplicate.
 */
export async function uploadFile<T>(file: File, fields: Record<string, string>): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  for (const [name, value] of Object.entries(fields)) form.append(name, value);
  const hash = await sha256Hex(file);
  if (hash) form.append("sha256", hash);

  let lastError: ApiError | undefined;
  for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt++) {
    if (attempt > 0) await sleep(RETRY_DELAYS_MS[attempt - 1]);
    let response: Response;
    try {
      response = await fetch("/api/v1/files", {
        method: "POST",
        body: form,
        credentials: "same-origin",
      });
    } catch {
      lastError = new ApiError(0, "network_error", NETWORK_ERROR);
      continue;
    }
    if (response.ok) return (await response.json()) as T;
    const error = await toApiError(response);
    if (!RETRYABLE_STATUSES.has(response.status)) throw error;
    lastError = error;
  }
  throw lastError ?? new ApiError(0, "network_error", NETWORK_ERROR);
}

/** Maps a validation response to messages per form field. */
export function fieldErrors(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError) || !Array.isArray(error.details)) {
    return {};
  }
  const result: Record<string, string> = {};
  for (const item of error.details as { field?: string; message?: string }[]) {
    if (item.field && item.message) {
      result[item.field] = item.message.replace(/^Value error, /, "");
    }
  }
  return result;
}
