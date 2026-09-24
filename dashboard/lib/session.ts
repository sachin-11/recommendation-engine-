// The dashboard session is an API key kept in localStorage and sent as X-API-Key.
// Every access is guarded: storage can be unavailable (private mode, blocked site data).

const KEY = "reco.apiKey";
export const SESSION_EVENT = "reco:session";

export function getApiKey(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setApiKey(value: string): void {
  try {
    window.localStorage.setItem(KEY, value);
  } catch {
    // Storage unavailable: the session only lasts for this page load.
  }
  window.dispatchEvent(new Event(SESSION_EVENT));
}

export function clearApiKey(): void {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    // Nothing stored.
  }
  window.dispatchEvent(new Event(SESSION_EVENT));
}
