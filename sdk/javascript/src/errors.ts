import type { ApiErrorDetail } from "./types";

export interface ErrorInit {
  status?: number;
  code?: string;
  details?: ApiErrorDetail[];
  requestId?: string;
  cause?: unknown;
}

/** Base class for every error thrown by the SDK. */
export class RecoEngineError extends Error {
  /** HTTP status, or undefined when the API could not be reached. */
  readonly status?: number;
  /** API error code, e.g. "validation_error", "not_found". */
  readonly code?: string;
  readonly details: ApiErrorDetail[];
  /** X-Request-ID of the failed request; include it when contacting support. */
  readonly requestId?: string;

  constructor(message: string, init: ErrorInit = {}) {
    super(message, init.cause === undefined ? undefined : { cause: init.cause });
    this.name = new.target.name;
    this.status = init.status;
    this.code = init.code;
    this.details = init.details ?? [];
    this.requestId = init.requestId;
  }
}

/** 401 or 403: missing, invalid, revoked or expired API key, or an inactive tenant. */
export class AuthError extends RecoEngineError {}

/** 404: the item, batch or query does not exist (for this tenant). */
export class NotFoundError extends RecoEngineError {}

/** 400, 409 or 422: the request was rejected; see `details`. */
export class ValidationError extends RecoEngineError {}

/** 429 after all retries. `retryAfter` is in seconds. */
export class RateLimitError extends RecoEngineError {
  readonly retryAfter?: number;

  constructor(message: string, init: ErrorInit & { retryAfter?: number } = {}) {
    super(message, init);
    this.retryAfter = init.retryAfter;
  }
}

/** 503 after all retries: OpenAI or Pinecone is unavailable or timed out. */
export class ServiceUnavailableError extends RecoEngineError {}

/** The API could not be reached (DNS, connection refused, timeout). */
export class ConnectionError extends RecoEngineError {}
