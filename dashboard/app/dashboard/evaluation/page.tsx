"use client";

import * as React from "react";
import { ClipboardCheck, Loader2, Play, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input, NativeSelect } from "@/components/ui/input";
import { FieldError, Label } from "@/components/ui/label";
import { EmptyState, PageHeader, TagInput } from "@/components/ui/misc";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toastApiError } from "@/lib/api";
import { useMe } from "@/lib/hooks/account";
import { useDeleteEvalQuery, useEvalQueries, useRunEvaluation, useSaveEvalQuery } from "@/lib/hooks/evaluation";
import { can, needsRole } from "@/lib/roles";
import type { EvalMetrics, EvalQuery, EvalRun } from "@/types";

const METRICS: { key: keyof EvalMetrics; label: string; hint: string }[] = [
  { key: "ndcg", label: "NDCG", hint: "Most relevant items at the top; 1 is the best possible order" },
  { key: "recall", label: "Recall", hint: "Share of the relevant items found" },
  { key: "mrr", label: "MRR", hint: "How soon the first relevant item appears" },
];

const VARIANT_HINTS: Record<string, string> = {
  vector: "Similarity alone",
  hybrid: "Vector + keyword, no feedback",
  current: "Your saved settings",
};

/** "job_101:3" or "job_101" (grade 1) -> [id, grade]; null when the grade is not 1–3. */
function parseTag(tag: string): [string, number] | null {
  const match = /^(.+?)(?::\s*([0-9]+))?$/.exec(tag.trim());
  if (!match) return null;
  const grade = match[2] ? Number(match[2]) : 1;
  return grade >= 1 && grade <= 3 ? [match[1].trim(), grade] : null;
}

function score(value: number) {
  return value.toFixed(3);
}

function AddQueryCard({ disabled, reason }: { disabled: boolean; reason?: string }) {
  const [query, setQuery] = React.useState("");
  const [tags, setTags] = React.useState<string[]>([]);
  const save = useSaveEvalQuery();
  const parsed = tags.map(parseTag);
  const invalid = tags.filter((_, i) => parsed[i] === null);
  const ready = query.trim() && tags.length > 0 && invalid.length === 0;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!ready) return;
    const relevant = Object.fromEntries(parsed.filter((p): p is [string, number] => p !== null));
    save.mutate(
      { query: query.trim(), relevant },
      {
        onSuccess: () => {
          toast.success("Saved to the golden set");
          setQuery("");
          setTags([]);
        },
        onError: (error) => toastApiError(error, "Could not save the query"),
      },
    );
  };

  return (
    <Card>
      <form onSubmit={submit}>
        <CardHeader>
          <CardTitle className="text-base">Add a golden query</CardTitle>
          <CardDescription>
            A real query and the items a good answer contains. Saving the same query again replaces its items.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="space-y-2">
            <Label htmlFor="evalQuery">Query</Label>
            <Input
              id="evalQuery"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="senior python developer with fastapi"
              maxLength={1000}
              disabled={disabled}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="evalRelevant">Relevant items</Label>
            <TagInput
              id="evalRelevant"
              value={tags}
              onChange={setTags}
              placeholder="job_101:3, then Enter"
              invalid={invalid.length > 0}
            />
            {invalid.length > 0 ? (
              <FieldError message={`Grades are 1 to 3: ${invalid.join(", ")}`} />
            ) : (
              <p className="text-xs text-muted-foreground">
                External id, optionally with a grade: 1 relevant (default), 2 very relevant, 3 perfect.
              </p>
            )}
          </div>
        </CardContent>
        <CardFooter className="justify-end">
          <Button type="submit" disabled={!ready || disabled || save.isPending} title={reason}>
            {save.isPending ? <Loader2 className="animate-spin" /> : <Plus />}
            Save query
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}

function GoldenSet({ queries, canEdit }: { queries: EvalQuery[]; canEdit: boolean }) {
  const remove = useDeleteEvalQuery();
  const [deleting, setDeleting] = React.useState<EvalQuery | null>(null);
  return (
    <>
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="pl-5">Query</TableHead>
            <TableHead>Relevant items</TableHead>
            <TableHead className="w-12 pr-5" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {queries.map((q) => (
            <TableRow key={q.id}>
              <TableCell className="pl-5 font-medium">{q.query}</TableCell>
              <TableCell>
                <div className="flex flex-wrap gap-1">
                  {Object.entries(q.relevant)
                    .sort((a, b) => b[1] - a[1])
                    .map(([id, grade]) => (
                      <Badge key={id} variant="secondary" className="font-mono text-xs">
                        {id} · {grade}
                      </Badge>
                    ))}
                </div>
              </TableCell>
              <TableCell className="pr-5 text-right">
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Delete “${q.query}”`}
                  disabled={!canEdit}
                  onClick={() => setDeleting(q)}
                >
                  <Trash2 />
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title="Delete this golden query?"
        description={deleting ? `“${deleting.query}” leaves the golden set.` : undefined}
        confirmLabel="Delete"
        destructive
        pending={remove.isPending}
        onConfirm={() =>
          deleting &&
          remove.mutate(deleting.id, {
            onSuccess: () => setDeleting(null),
            onError: (error) => toastApiError(error, "Could not delete the query"),
          })
        }
      />
    </>
  );
}

/** A value with a thin meter behind it: one hue, so the cells compare at a glance. */
function MetricCell({ value, best }: { value: number; best: boolean }) {
  return (
    <TableCell className="text-right">
      <div className="ml-auto flex w-32 flex-col items-end gap-1">
        <span className={best ? "font-semibold tabular-nums" : "tabular-nums text-muted-foreground"}>
          {score(value)}
        </span>
        <div className="h-1.5 w-full rounded-full bg-muted" aria-hidden>
          <div className="h-full rounded-full bg-primary" style={{ width: `${Math.round(value * 100)}%` }} />
        </div>
      </div>
    </TableCell>
  );
}

function Results({ run }: { run: EvalRun }) {
  const best = Object.fromEntries(
    METRICS.map(({ key }) => [key, Math.max(...run.variants.map((v) => v[key]))]),
  ) as Record<keyof EvalMetrics, number>;
  const names = run.variants.map((v) => v.name);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Results</CardTitle>
          <CardDescription>
            Averages over {run.queries} {run.queries === 1 ? "query" : "queries"}, top {run.k} results. Higher is
            better; the best per metric is bold.
          </CardDescription>
        </CardHeader>
        <CardContent className="px-0">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="pl-5">Variant</TableHead>
                {METRICS.map((m) => (
                  <TableHead key={m.key} className="text-right" title={m.hint}>
                    {m.label}@{run.k}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {run.variants.map((v) => (
                <TableRow key={v.name}>
                  <TableCell className="pl-5">
                    <p className="font-medium">{v.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {VARIANT_HINTS[v.name] ?? ""}
                      {v.ranking.enabled && v.ranking.keyword > 0 ? ` · keyword ${v.ranking.keyword}` : ""}
                    </p>
                  </TableCell>
                  {METRICS.map(({ key }) => (
                    <MetricCell key={key} value={v[key]} best={v[key] === best[key] && best[key] > 0} />
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Per query</CardTitle>
          <CardDescription>NDCG per variant. Read the queries a variant loses on: an average hides them.</CardDescription>
        </CardHeader>
        <CardContent className="px-0">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="pl-5">Query</TableHead>
                {names.map((name) => (
                  <TableHead key={name} className="text-right">
                    {name}
                  </TableHead>
                ))}
                <TableHead className="pr-5">Top results ({names[names.length - 1]})</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {run.per_query.map((row) => {
                const top = Math.max(...names.map((n) => row.by_variant[n].ndcg));
                const last = names[names.length - 1];
                return (
                  <TableRow key={row.id}>
                    <TableCell className="pl-5 font-medium">{row.query}</TableCell>
                    {names.map((name) => {
                      const value = row.by_variant[name].ndcg;
                      return (
                        <TableCell
                          key={name}
                          className={
                            value === top && top > 0
                              ? "text-right font-semibold tabular-nums"
                              : "text-right tabular-nums text-muted-foreground"
                          }
                        >
                          {score(value)}
                        </TableCell>
                      );
                    })}
                    <TableCell className="pr-5">
                      <div className="flex flex-wrap gap-1">
                        {row.top[last].slice(0, 5).map((id) => (
                          <Badge
                            key={id}
                            variant={id in row.relevant ? "success" : "outline"}
                            className="font-mono text-xs"
                          >
                            {id}
                          </Badge>
                        ))}
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
          <p className="px-5 pt-3 text-xs text-muted-foreground">Green: in the golden set for that query.</p>
        </CardContent>
      </Card>
    </div>
  );
}

export default function EvaluationPage() {
  const { data: me } = useMe();
  const canEdit = can(me, "DEVELOPER");
  const reason = canEdit ? undefined : needsRole("DEVELOPER");
  const { data: queries, isLoading } = useEvalQueries();
  const run = useRunEvaluation();
  const [k, setK] = React.useState(10);

  const start = () =>
    run.mutate(k, { onError: (error) => toastApiError(error, "Could not run the evaluation") });

  return (
    <>
      <PageHeader
        title="Evaluation"
        description="Score ranking settings on queries with known good answers before your users see them."
        actions={
          <div className="flex items-center gap-2">
            <NativeSelect value={k} onChange={(e) => setK(Number(e.target.value))} className="w-28" aria-label="Top k">
              <option value={5}>Top 5</option>
              <option value={10}>Top 10</option>
              <option value={20}>Top 20</option>
            </NativeSelect>
            <Button onClick={start} disabled={!queries?.length || run.isPending || !canEdit} title={reason}>
              {run.isPending ? <Loader2 className="animate-spin" /> : <Play />}
              Run evaluation
            </Button>
          </div>
        }
      />
      <div className="space-y-6">
        {run.data && <Results run={run.data} />}

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Golden set</CardTitle>
            <CardDescription>
              {queries?.length ?? 0} of 200 queries. Use real queries, including hard ones: codes, names, rare skills.
            </CardDescription>
          </CardHeader>
          <CardContent className="px-0">
            {isLoading ? (
              <div className="space-y-2 px-5">
                {Array.from({ length: 3 }, (_, i) => (
                  <Skeleton key={i} className="h-10 w-full" />
                ))}
              </div>
            ) : queries?.length ? (
              <GoldenSet queries={queries} canEdit={canEdit} />
            ) : (
              <EmptyState
                className="mx-5"
                icon={ClipboardCheck}
                title="No golden queries yet"
                description="Add queries and the items you expect for them, then run an evaluation."
              />
            )}
          </CardContent>
        </Card>

        <AddQueryCard disabled={!canEdit} reason={reason} />
      </div>
    </>
  );
}
