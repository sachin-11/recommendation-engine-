"use client";

import * as React from "react";
import Link from "next/link";
import { AlertTriangle, Loader2, MessageSquareText, Sparkles, X } from "lucide-react";

import { ResultCard, ResultCardSkeleton } from "@/components/recommend/ResultCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { NativeSelect, Textarea } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { EmptyState } from "@/components/ui/misc";
import { apiErrorMessage } from "@/lib/api";
import { useAskStream } from "@/lib/hooks/ask";
import { useRecommend } from "@/lib/hooks/recommend";
import type { Recommendation, Tenant } from "@/types";

const EXAMPLES: Record<string, string[]> = {
  HR: ["remote python job, Delhi or Pune is fine too", "full-time data roles, not in Mumbai"],
  FOOD: ["something spicy and vegetarian under 300", "a light dessert without nuts"],
  ECOMMERCE: ["running shoes for flat feet, under 5000", "a gift for a coffee lover"],
  EDTECH: ["beginner machine learning course in Hindi", "advanced SQL for analysts"],
};

/** A filter as a person would say it: "location: Delhi, Pune", "experience_years: 3–5". */
export function describeFilter(field: string, condition: unknown): string {
  if (Array.isArray(condition)) return `${field}: ${condition.join(", ")}`;
  if (condition && typeof condition === "object") {
    const c = condition as Record<string, unknown>;
    if (Array.isArray(c.nin)) return `${field}: not ${(c.nin as unknown[]).join(", ")}`;
    if (c.ne !== undefined) return `${field}: not ${String(c.ne)}`;
    const low = c.gte ?? c.gt;
    const high = c.lte ?? c.lt;
    if (low !== undefined && high !== undefined) return `${field}: ${String(low)}–${String(high)}`;
    if (low !== undefined) return `${field}: ${c.gte !== undefined ? "≥" : ">"} ${String(low)}`;
    if (high !== undefined) return `${field}: ${c.lte !== undefined ? "≤" : "<"} ${String(high)}`;
  }
  return `${field}: ${String(condition)}`;
}

/** The answer with **bold** shown as bold; nothing else is interpreted. */
function AnswerText({ text }: { text: string }) {
  const parts = text.split(/\*\*(.+?)\*\*/g);
  return (
    <>
      {parts.map((part, i) => (i % 2 ? <strong key={i}>{part}</strong> : <React.Fragment key={i}>{part}</React.Fragment>))}
    </>
  );
}

function Cursor() {
  return <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse rounded-sm bg-primary align-text-bottom" aria-hidden />;
}

export function AskPanel({ tenant }: { tenant: Tenant }) {
  const [question, setQuestion] = React.useState("");
  const [topK, setTopK] = React.useState(6);
  const stream = useAskStream();
  // Results again after the user removed a filter chip; null shows the streamed ones.
  const refine = useRecommend();
  const [removed, setRemoved] = React.useState<string[]>([]);
  const config = tenant.domain_config;
  const examples = EXAMPLES[tenant.domain_type] ?? [];
  const busy = stream.status === "searching" || stream.status === "writing";

  const submit = (text = question) => {
    const q = text.trim();
    if (!q) return;
    setQuestion(q);
    setRemoved([]);
    refine.reset();
    void stream.ask(q, topK);
  };

  const interpretation = stream.interpretation;
  const activeFilters = Object.entries(interpretation?.filters ?? {}).filter(([f]) => !removed.includes(f));
  const removeFilter = (field: string) => {
    if (!interpretation) return;
    const next = [...removed, field];
    setRemoved(next);
    refine.mutate({
      type: "text",
      query: interpretation.search_text,
      top_k: topK,
      filters: Object.fromEntries(Object.entries(interpretation.filters).filter(([f]) => !next.includes(f))),
      include_raw_data: true,
    });
  };

  const shown = refine.data ?? stream.results;
  const results: Recommendation[] = shown?.results ?? [];

  return (
    <div className="space-y-6">
      <Card>
        <CardContent className="space-y-3 p-4">
          <Label htmlFor="askQuestion">Ask in your own words</Label>
          <Textarea
            id="askQuestion"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit();
            }}
            rows={3}
            maxLength={1000}
            placeholder={examples[0] ?? `Describe the ${config.item_label} you want`}
          />
          <div className="flex flex-wrap items-center gap-2">
            {examples.map((example) => (
              <Button key={example} variant="outline" size="sm" onClick={() => submit(example)} disabled={busy}>
                {example}
              </Button>
            ))}
            <div className="ml-auto flex items-center gap-2">
              <NativeSelect
                value={topK}
                onChange={(e) => setTopK(Number(e.target.value))}
                className="w-28"
                aria-label="Number of results"
              >
                {[3, 6, 9, 12].map((n) => (
                  <option key={n} value={n}>
                    {n} results
                  </option>
                ))}
              </NativeSelect>
              <Button onClick={() => submit()} disabled={!question.trim() || busy}>
                {busy ? <Loader2 className="animate-spin" /> : <Sparkles />}
                Ask
              </Button>
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            A language model reads your question into a search and filters, then writes a short answer. Ctrl/⌘ +
            Enter to ask.
          </p>
        </CardContent>
      </Card>

      {stream.status === "idle" ? (
        <EmptyState
          icon={MessageSquareText}
          title={`Ask for ${config.item_label}s the way a user would`}
          description="Locations, ranges and exclusions in the question become filters; you will see what was understood and can remove any of it."
        />
      ) : stream.status === "error" ? (
        <EmptyState
          icon={AlertTriangle}
          title="The question could not be answered"
          description={stream.error ?? "Something went wrong."}
          action={
            stream.errorCode === "plan_required" ? (
              <Button asChild>
                <Link href="/dashboard/billing">
                  <Sparkles /> See plans
                </Link>
              </Button>
            ) : (
              <Button variant="outline" onClick={() => submit()}>
                Try again
              </Button>
            )
          }
        />
      ) : (
        <>
          {interpretation && (
            <div className="space-y-2 rounded-lg border p-4" aria-label="What was understood">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-muted-foreground">Searched for</span>
                <span className="font-medium">“{interpretation.search_text}”</span>
                {activeFilters.map(([field, condition]) => (
                  <Badge key={field} variant="secondary" className="gap-1 pr-1 font-normal">
                    {describeFilter(field, condition)}
                    <button
                      type="button"
                      onClick={() => removeFilter(field)}
                      className="rounded-sm p-0.5 hover:bg-muted"
                      aria-label={`Remove filter ${field}`}
                      disabled={refine.isPending}
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </Badge>
                ))}
              </div>
              {interpretation.fallback && (
                <p className="text-xs text-muted-foreground">
                  The question could not be read ({interpretation.fallback}), so it was searched as written.
                </p>
              )}
              {interpretation.ignored.length > 0 && (
                <ul className="space-y-0.5 text-xs text-warning">
                  {interpretation.ignored.map((note) => (
                    <li key={note} className="flex items-center gap-1.5">
                      <AlertTriangle className="h-3 w-3 shrink-0" /> Not applied: {note}
                    </li>
                  ))}
                </ul>
              )}
              {stream.results?.relaxed && !refine.data && (
                <p className="text-xs text-warning">
                  Nothing matched all the filters, so these results are without them.
                </p>
              )}
            </div>
          )}

          {!refine.data && (stream.summary || stream.status === "writing" || stream.summaryFailed) && (
            <div className="flex gap-3 rounded-lg bg-primary/5 p-4 text-sm" aria-live="polite">
              <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
              <p className="leading-relaxed">
                {stream.summaryFailed && !stream.summary ? (
                  <span className="text-muted-foreground">An answer could not be written; the results are below.</span>
                ) : (
                  <>
                    <AnswerText text={stream.summary} />
                    {stream.status === "writing" && <Cursor />}
                  </>
                )}
              </p>
            </div>
          )}
          {refine.data && (
            <p className="text-xs text-muted-foreground">
              Results with your changed filters. The written answer was for the original question.
            </p>
          )}
          {refine.isError && <p className="text-sm text-destructive">{apiErrorMessage(refine.error)}</p>}

          {stream.status === "searching" || refine.isPending ? (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {Array.from({ length: Math.min(topK, 6) }, (_, i) => (
                <ResultCardSkeleton key={i} />
              ))}
            </div>
          ) : shown && results.length ? (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {results.map((r) => (
                <ResultCard key={`${shown.query_id}-${r.external_id}`} result={r} queryId={shown.query_id} config={config} />
              ))}
            </div>
          ) : shown ? (
            <EmptyState icon={MessageSquareText} title="No matches" description="Nothing in your catalogue fits this question." />
          ) : null}
        </>
      )}
    </div>
  );
}
