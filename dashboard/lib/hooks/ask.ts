"use client";

import * as React from "react";

import { getApiKey } from "@/lib/session";
import { API_BASE_URL } from "@/lib/utils";
import type { AskInterpretation, AskResults } from "@/types";

export type AskStatus = "idle" | "searching" | "writing" | "done" | "error";

export interface AskState {
  status: AskStatus;
  question: string;
  interpretation: AskInterpretation | null;
  results: AskResults | null;
  summary: string;
  /** The summary could not be written; results may still be there. */
  summaryFailed: boolean;
  error: string | null;
  /** The API's error code, e.g. "plan_required". */
  errorCode: string | null;
  summaryTokens: number;
}

const INITIAL: AskState = {
  status: "idle",
  question: "",
  interpretation: null,
  results: null,
  summary: "",
  summaryFailed: false,
  error: null,
  errorCode: null,
  summaryTokens: 0,
};

/** Splits a Server-Sent Events buffer into complete events and the unfinished rest. */
export function parseEvents(buffer: string): { events: { name: string; data: unknown }[]; rest: string } {
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";
  const events = blocks
    .map((block) => {
      let name = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) name = line.slice(7);
        else if (line.startsWith("data: ")) data.push(line.slice(6));
      }
      return data.length ? { name, data: JSON.parse(data.join("\n")) as unknown } : null;
    })
    .filter((e): e is { name: string; data: unknown } => e !== null);
  return { events, rest };
}

async function readError(response: Response): Promise<{ message: string; code: string | null }> {
  try {
    const body = (await response.json()) as { error?: { message?: string; code?: string } };
    if (body.error?.message) return { message: body.error.message, code: body.error.code ?? null };
  } catch {
    // Not JSON.
  }
  return { message: `The request failed (${response.status}).`, code: null };
}

/** POST /recommend/ask/stream: results first, then the written answer as it arrives. */
export function useAskStream() {
  const [state, setState] = React.useState<AskState>(INITIAL);
  const controller = React.useRef<AbortController | null>(null);

  React.useEffect(() => () => controller.current?.abort(), []);

  const ask = React.useCallback(async (question: string, topK: number) => {
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    setState({ ...INITIAL, status: "searching", question });
    const key = getApiKey();
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/recommend/ask/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(key ? { "X-API-Key": key } : {}) },
        // Item details show titles on the result cards.
        body: JSON.stringify({ question, top_k: topK, include_raw_data: true }),
        signal: abort.signal,
      });
      if (!response.ok || !response.body) {
        const { message, code } = await readError(response);
        setState((s) => ({ ...s, status: "error", error: message, errorCode: code }));
        return;
      }
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        const parsed = parseEvents(buffer + value);
        buffer = parsed.rest;
        for (const { name, data } of parsed.events) {
          setState((s) => {
            switch (name) {
              case "interpretation":
                return { ...s, interpretation: data as AskInterpretation };
              case "results":
                return { ...s, results: data as AskResults, status: "writing" };
              case "summary":
                return { ...s, summary: s.summary + (data as { text: string }).text };
              case "done":
                return { ...s, status: "done", summaryTokens: (data as { summary_tokens: number }).summary_tokens };
              case "error":
                return { ...s, status: "done", summaryFailed: true };
              default:
                return s;
            }
          });
        }
      }
      // A stream that ended without "done" or "error" was cut off.
      setState((s) => (s.status === "writing" ? { ...s, status: "done", summaryFailed: true } : s));
    } catch (error) {
      if (abort.signal.aborted) return;
      setState((s) => ({ ...s, status: "error", error: (error as Error).message || "Network error" }));
    }
  }, []);

  const reset = React.useCallback(() => {
    controller.current?.abort();
    setState(INITIAL);
  }, []);

  return { ...state, ask, reset };
}
