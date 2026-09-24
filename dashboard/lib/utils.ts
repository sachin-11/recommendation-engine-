import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDate(value: string | null | undefined, withTime = true): string {
  if (!value) return "—";
  const date = new Date(value);
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  });
}

export function formatRelative(value: string | null | undefined): string {
  if (!value) return "Never";
  const seconds = Math.round((Date.now() - new Date(value).getTime()) / 1000);
  if (seconds < 60) return "Just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} d ago`;
  return formatDate(value, false);
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat().format(value);
}

export const APP_NAME = process.env.NEXT_PUBLIC_APP_NAME || "RecoEngine";
// An empty value means "same origin" (the API behind the same reverse proxy as the dashboard).
export const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);
/** Absolute API origin for code samples shown to users (same origin when unset). */
export function publicApiUrl(): string {
  if (API_BASE_URL) return API_BASE_URL;
  return typeof window === "undefined" ? "" : window.location.origin;
}
/** Public API documentation; defaults to the API's own Swagger UI. */
export const DOCS_URL = process.env.NEXT_PUBLIC_DOCS_URL || `${API_BASE_URL}/docs`;
