import * as React from "react";

import { cn } from "@/lib/utils";

// Keys, strings, numbers, booleans/null. Everything else is punctuation or whitespace.
const TOKEN = /("(?:\\.|[^"\\])*"(?:\s*:)?|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\btrue\b|\bfalse\b|\bnull\b)/g;

function highlight(json: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let last = 0;
  let index = 0;
  for (const match of json.matchAll(TOKEN)) {
    const start = match.index ?? 0;
    if (start > last) nodes.push(json.slice(last, start));
    const token = match[0];
    let className = "text-sky-600 dark:text-sky-400"; // number
    if (token.startsWith('"')) {
      className = token.trimEnd().endsWith(":")
        ? "text-violet-600 dark:text-violet-300"
        : "text-emerald-700 dark:text-emerald-400";
    } else if (token === "true" || token === "false" || token === "null") {
      className = "text-amber-600 dark:text-amber-400";
    }
    nodes.push(
      <span key={index++} className={className}>
        {token}
      </span>,
    );
    last = start + token.length;
  }
  if (last < json.length) nodes.push(json.slice(last));
  return nodes;
}

/** Read-only, syntax-highlighted JSON. Text is rendered as React nodes, never as HTML. */
export function JsonView({ value, className }: { value: unknown; className?: string }) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <pre
      className={cn(
        "overflow-auto rounded-md border bg-muted/40 p-3 font-mono text-xs leading-relaxed",
        className,
      )}
    >
      <code>{highlight(text)}</code>
    </pre>
  );
}
