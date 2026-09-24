import axios, { AxiosError } from "axios";
import { toast } from "sonner";

import { API_BASE_URL, publicApiUrl } from "@/lib/utils";
import { clearApiKey, getApiKey } from "@/lib/session";
import type { ApiErrorBody } from "@/types";

export const api = axios.create({
  baseURL: `${API_BASE_URL}/api/v1`,
  timeout: 30_000,
  headers: { "Content-Type": "application/json" },
});

api.interceptors.request.use((config) => {
  const key = getApiKey();
  // A request may bring its own key (signing in with an API key); don't overwrite it.
  if (key && !config.headers.has("X-API-Key")) config.headers.set("X-API-Key", key);
  return config;
});

// Requests whose 401 means "wrong credentials", not "session expired".
const AUTH_PATHS = ["/auth/login", "/auth/register"];

api.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ApiErrorBody>) => {
    const status = error.response?.status;
    const url = error.config?.url ?? "";
    if (status === 401 && !AUTH_PATHS.some((path) => url.endsWith(path))) {
      clearApiKey();
      if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
        toast.error("Your session has ended. Please sign in again.", { id: "session" });
        window.location.assign(`/login?next=${encodeURIComponent(window.location.pathname)}`);
      }
    } else if (status === 429) {
      const retry = error.response?.headers["retry-after"];
      toast.warning("Rate limit reached", {
        id: "rate-limit",
        description: retry ? `Try again in ${retry} seconds.` : "Please slow down and retry shortly.",
      });
    } else if (status === 503) {
      toast.error("Service temporarily unavailable", {
        id: "unavailable",
        description: apiErrorMessage(error),
      });
    } else if (!error.response) {
      toast.error("Cannot reach the API", {
        id: "network",
        description: `Is the backend running at ${publicApiUrl()}?`,
      });
    }
    return Promise.reject(error);
  },
);

/** The human-readable message from an API error, including field details. */
export function apiErrorMessage(error: unknown): string {
  if (axios.isAxiosError<ApiErrorBody>(error)) {
    const body = error.response?.data?.error;
    if (body) {
      const details = (body.details ?? [])
        .filter((d) => d.type !== "expected_columns")
        .map((d) => (d.field ? `${d.field}: ${d.message}` : d.message));
      return details.length ? `${body.message}. ${details.join("; ")}` : body.message;
    }
    if (!error.response) return "Network error: the API could not be reached.";
    return error.message;
  }
  return error instanceof Error ? error.message : "Something went wrong";
}

/** Toast every error the interceptor has not already announced. */
export function toastApiError(error: unknown, title = "Request failed"): void {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status;
    if (status === 401 || status === 429 || status === 503 || !error.response) return;
  }
  toast.error(title, { description: apiErrorMessage(error) });
}
