import axios, { type AxiosInstance, type AxiosRequestConfig, type AxiosResponse } from "axios";

import {
  AuthError,
  ConnectionError,
  NotFoundError,
  RateLimitError,
  RecoEngineError,
  ServiceUnavailableError,
  ValidationError,
} from "./errors";
import { Analytics } from "./resources/analytics";
import { Items } from "./resources/items";
import { Recommend } from "./resources/recommend";
import type { ApiErrorDetail, ClientOptions } from "./types";

export const DEFAULT_BASE_URL = "https://api.recoengine.io";
export const SDK_VERSION = "0.1.0";

const RETRY_STATUSES = new Set([429, 503]);

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Seconds from a Retry-After header (delta-seconds or HTTP date), if present. */
function parseRetryAfter(value: unknown): number | undefined {
  if (typeof value !== "string" || !value.trim()) return undefined;
  const seconds = Number(value);
  if (Number.isFinite(seconds)) return Math.max(0, seconds);
  const date = Date.parse(value);
  return Number.isNaN(date) ? undefined : Math.max(0, (date - Date.now()) / 1000);
}

/**
 * Client for the RecoEngine API.
 *
 * ```ts
 * const client = new RecoEngineClient({ apiKey: process.env.RECO_API_KEY! });
 * const { results } = await client.recommend.byText("senior python developer", { topK: 5 });
 * ```
 */
export class RecoEngineClient {
  readonly items: Items;
  readonly recommend: Recommend;
  readonly analytics: Analytics;

  private readonly http: AxiosInstance;
  private readonly maxRetries: number;
  private readonly retryBaseDelayMs: number;
  private readonly maxRetryDelayMs: number;
  private readonly sleep: (ms: number) => Promise<void>;

  constructor(options: ClientOptions) {
    if (!options?.apiKey) {
      throw new RecoEngineError("apiKey is required. Create one in the dashboard under API Keys.");
    }
    const baseUrl = (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
    const headers: Record<string, string> = { "X-API-Key": options.apiKey };
    // Browsers forbid setting User-Agent; only send it from Node.
    if (typeof window === "undefined") headers["User-Agent"] = `recoengine-js/${SDK_VERSION}`;

    this.http = axios.create({ baseURL: `${baseUrl}/api/v1`, timeout: options.timeout ?? 10_000, headers });
    this.maxRetries = options.maxRetries ?? 3;
    this.retryBaseDelayMs = options.retryBaseDelayMs ?? 500;
    this.maxRetryDelayMs = options.maxRetryDelayMs ?? 30_000;
    this.sleep = options.sleep ?? defaultSleep;

    this.items = new Items(this);
    this.recommend = new Recommend(this);
    this.analytics = new Analytics(this);
  }

  /**
   * Send a request, retrying 429 and 503 up to `maxRetries` times. Waits for Retry-After
   * when the API sends it, otherwise backs off exponentially with jitter.
   * @internal
   */
  async request<T>(config: AxiosRequestConfig): Promise<AxiosResponse<T>> {
    for (let attempt = 0; ; attempt++) {
      try {
        return await this.http.request<T>(config);
      } catch (error) {
        const status = axios.isAxiosError(error) ? error.response?.status : undefined;
        if (status !== undefined && RETRY_STATUSES.has(status) && attempt < this.maxRetries) {
          const retryAfter = axios.isAxiosError(error)
            ? parseRetryAfter(error.response?.headers?.["retry-after"])
            : undefined;
          await this.sleep(this.retryDelay(attempt, retryAfter));
          continue;
        }
        throw toRecoEngineError(error);
      }
    }
  }

  private retryDelay(attempt: number, retryAfterSeconds?: number): number {
    const delay =
      retryAfterSeconds !== undefined
        ? retryAfterSeconds * 1000
        : this.retryBaseDelayMs * 2 ** attempt * (0.5 + Math.random() / 2);
    return Math.min(delay, this.maxRetryDelayMs);
  }
}

interface ApiErrorBody {
  error?: { code?: string; message?: string; details?: ApiErrorDetail[] };
}

/** Map an axios error to the SDK's error classes. */
export function toRecoEngineError(error: unknown): RecoEngineError {
  if (error instanceof RecoEngineError) return error;
  if (!axios.isAxiosError(error)) {
    return new RecoEngineError(error instanceof Error ? error.message : String(error), { cause: error });
  }
  const response = error.response;
  if (!response) {
    const reason = error.code === "ECONNABORTED" ? "timed out" : error.message;
    return new ConnectionError(`Could not reach the RecoEngine API: ${reason}`, { cause: error });
  }
  const body = (response.data ?? {}) as ApiErrorBody;
  const init = {
    status: response.status,
    code: body.error?.code,
    details: body.error?.details ?? [],
    requestId: response.headers?.["x-request-id"] as string | undefined,
    cause: error,
  };
  const message = body.error?.message ?? `HTTP ${response.status}`;
  switch (response.status) {
    case 401:
    case 403:
      return new AuthError(message, init);
    case 404:
      return new NotFoundError(message, init);
    case 400:
    case 409:
    case 422:
      return new ValidationError(message, init);
    case 429:
      return new RateLimitError(message, {
        ...init,
        retryAfter: parseRetryAfter(response.headers?.["retry-after"]),
      });
    case 503:
      return new ServiceUnavailableError(message, init);
    default:
      return new RecoEngineError(message, init);
  }
}
