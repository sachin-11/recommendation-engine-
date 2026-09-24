"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ChevronDown,
  FileText,
  Loader2,
  Search,
  Sparkles,
  UserRound,
  Zap,
} from "lucide-react";

import {
  ResultCard,
  ResultCardSkeleton,
} from "@/components/recommend/ResultCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox, Slider } from "@/components/ui/controls";
import { Input, Textarea } from "@/components/ui/input";
import { FieldError, Label } from "@/components/ui/label";
import { CopyButton, EmptyState } from "@/components/ui/misc";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { apiErrorMessage } from "@/lib/api";
import { presetFor } from "@/lib/domains";
import { buildFilters } from "@/lib/filters";
import { useItems } from "@/lib/hooks/items";
import { useRecommend } from "@/lib/hooks/recommend";
import { cn } from "@/lib/utils";
import type { Item, RecommendRequest, RecommendResult, Tenant } from "@/types";

type Tab = "text" | "item" | "profile";

function useDebounced<T>(value: T, delay = 250): T {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

function FilterInputs({
  fields,
  values,
  onChange,
}: {
  fields: string[];
  values: Record<string, string>;
  onChange: (values: Record<string, string>) => void;
}) {
  if (!fields.length) {
    return (
      <p className="text-xs text-muted-foreground">
        No filter fields configured.
      </p>
    );
  }
  return (
    <div className="space-y-3">
      {fields.map((field) => (
        <div key={field} className="space-y-1">
          <Label htmlFor={`filter-${field}`} className="font-mono text-xs">
            {field}
          </Label>
          <Input
            id={`filter-${field}`}
            value={values[field] ?? ""}
            onChange={(e) => onChange({ ...values, [field]: e.target.value })}
            placeholder="any"
            className="h-8 text-sm"
          />
        </div>
      ))}
      <p className="text-xs leading-relaxed text-muted-foreground">
        <code>Delhi</code> exact · <code>Delhi, Pune</code> any of ·{" "}
        <code>&gt;=3</code> or <code>3..8</code> range · <code>true</code>
      </p>
    </div>
  );
}

function itemTitle(item: Item, field: string): string | null {
  const value =
    item.raw_data[field] ?? item.raw_data.title ?? item.raw_data.name;
  return typeof value === "string" && value.trim() ? value : null;
}

/** Combobox: opens with every embedded item, narrows as you type. */
function ItemIdInput({
  value,
  onChange,
  onPick,
  titleField,
}: {
  value: string;
  onChange: (value: string) => void;
  onPick: (value: string) => void;
  titleField: string;
}) {
  const [open, setOpen] = React.useState(false);
  // What the list is filtered by: empty right after opening, so the full list shows.
  const [filter, setFilter] = React.useState("");
  const [active, setActive] = React.useState(0);
  const search = useDebounced(filter);
  const { data, isFetching } = useItems({ search, status: "DONE" }, 1);
  const options = open ? (data?.items ?? []) : [];
  const listId = "item-options";

  const openList = () => {
    setFilter("");
    setActive(0);
    setOpen(true);
  };
  const pick = (item: Item) => {
    onChange(item.external_id);
    onPick(item.external_id);
    setOpen(false);
  };

  return (
    <>
      <div className="relative">
        <Input
          id="externalId"
          value={value}
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          onFocus={openList}
          onClick={() => !open && openList()}
          onBlur={() => setOpen(false)}
          onChange={(e) => {
            onChange(e.target.value);
            setFilter(e.target.value);
            setActive(0);
            setOpen(true);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              if (!open) openList();
              else setActive((i) => Math.min(i + 1, options.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((i) => Math.max(i - 1, 0));
            } else if (e.key === "Enter" && open && options[active]) {
              e.preventDefault();
              pick(options[active]);
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
          placeholder="Click to pick, or type an external_id…"
          autoComplete="off"
          className="pr-8 font-mono"
        />
        <ChevronDown
          className={cn(
            "pointer-events-none absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
        {open && (
          <ul
            id={listId}
            role="listbox"
            className="absolute z-50 mt-1 max-h-72 w-full overflow-auto rounded-md border bg-popover p-1 text-popover-foreground shadow-md"
          >
            {options.length ? (
              options.map((item, index) => {
                const title = itemTitle(item, titleField);
                return (
                  <li
                    key={item.id}
                    role="option"
                    aria-selected={index === active}
                    // mousedown keeps focus in the input, so blur does not close the list first
                    onMouseDown={(e) => {
                      e.preventDefault();
                      pick(item);
                    }}
                    onMouseEnter={() => setActive(index)}
                    className={cn(
                      "cursor-pointer rounded-sm px-2 py-1.5 text-sm",
                      index === active && "bg-accent text-accent-foreground",
                      item.external_id === value && "font-medium",
                    )}
                  >
                    <div className="font-mono text-xs">{item.external_id}</div>
                    {title && (
                      <div className="truncate text-xs text-muted-foreground">
                        {title}
                      </div>
                    )}
                  </li>
                );
              })
            ) : (
              <li className="px-2 py-1.5 text-sm text-muted-foreground">
                {isFetching ? "Loading…" : "No embedded items match."}
              </li>
            )}
            {data && data.total > options.length && (
              <li className="border-t px-2 pb-1 pt-1.5 text-xs text-muted-foreground">
                Showing {options.length} of {data.total}. Type to narrow down.
              </li>
            )}
          </ul>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        Only embedded items are listed. The item itself is never returned.
      </p>
    </>
  );
}

function ResultsHeader({ result }: { result: RecommendResult }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <h2 className="mr-auto text-base font-semibold">
        {result.total} result{result.total === 1 ? "" : "s"}
      </h2>
      {result.cache && (
        <Badge
          variant={result.cache === "HIT" ? "success" : "muted"}
          title="X-Cache header"
        >
          <Zap /> Cache {result.cache}
        </Badge>
      )}
      <Badge variant="outline" className="font-mono">
        {result.latency_ms} ms
      </Badge>
      <Badge
        variant="outline"
        className="font-mono"
        title="OpenAI tokens used to embed this query"
      >
        {result.embedding_tokens} tokens
      </Badge>
      <span className="flex items-center gap-1 text-xs text-muted-foreground">
        query_id{" "}
        <code className="font-mono">{result.query_id.slice(0, 8)}…</code>
        <CopyButton
          value={result.query_id}
          className="h-6 w-6"
          label="query_id copied"
        />
      </span>
    </div>
  );
}

export function QueryPlayground({ tenant }: { tenant: Tenant }) {
  const router = useRouter();
  const params = useSearchParams();
  const config = tenant.domain_config;
  const initialTab =
    (["text", "item", "profile"] as const).find(
      (t) => t === params.get("tab"),
    ) ?? "text";
  const [tab, setTab] = React.useState<Tab>(initialTab);
  const [text, setText] = React.useState("");
  const [externalId, setExternalId] = React.useState(params.get("id") ?? "");
  const [profile, setProfile] = React.useState<Record<string, string>>({});
  const [topK, setTopK] = React.useState(10);
  const [filters, setFilters] = React.useState<Record<string, string>>({});
  const [withDetails, setWithDetails] = React.useState(true);
  const recommend = useRecommend();
  const autoRan = React.useRef(false);

  const request = (): RecommendRequest | null => {
    const common = {
      top_k: topK,
      filters: buildFilters(filters),
      include_raw_data: withDetails,
    };
    if (tab === "text")
      return text.trim()
        ? { type: "text", query: text.trim(), ...common }
        : null;
    if (tab === "item")
      return externalId.trim()
        ? { type: "item", external_id: externalId.trim(), ...common }
        : null;
    const filled = Object.fromEntries(
      Object.entries(profile).filter(([, v]) => v.trim()),
    );
    return Object.keys(filled).length
      ? { type: "profile", profile: filled, ...common }
      : null;
  };
  const current = request();
  const run = () => current && recommend.mutate(current);

  // Arriving from "Find similar" runs the query straight away.
  React.useEffect(() => {
    if (!autoRan.current && initialTab === "item" && externalId) {
      autoRan.current = true;
      recommend.mutate({
        type: "item",
        external_id: externalId,
        top_k: topK,
        filters: {},
        include_raw_data: true,
      });
    }
  }, [initialTab, externalId, topK, recommend]);

  const changeTab = (value: string) => {
    setTab(value as Tab);
    recommend.reset();
    router.replace(`/dashboard/recommend?tab=${value}`, { scroll: false });
  };

  const result = recommend.data;
  return (
    <div className="grid gap-6 lg:grid-cols-[22rem_1fr]">
      <Card className="h-fit">
        <CardContent className="space-y-5 p-5">
          <Tabs value={tab} onValueChange={changeTab}>
            <TabsList className="grid w-full grid-cols-3">
              <TabsTrigger value="text">
                <Search /> Text
              </TabsTrigger>
              <TabsTrigger value="item">
                <FileText /> Item
              </TabsTrigger>
              <TabsTrigger value="profile">
                <UserRound /> Profile
              </TabsTrigger>
            </TabsList>

            <TabsContent value="text" className="space-y-2">
              <Label htmlFor="query">Query</Label>
              <Textarea
                id="query"
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={5}
                placeholder={presetFor(tenant.domain_type).exampleQuery}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) run();
                }}
              />
              <p className="text-xs text-muted-foreground">
                Ctrl/⌘ + Enter to run.
              </p>
            </TabsContent>

            <TabsContent value="item" className="space-y-2">
              <Label htmlFor="externalId">Similar to item</Label>
              <ItemIdInput
                value={externalId}
                onChange={setExternalId}
                titleField={config.primary_embedding_field}
                onPick={(id) =>
                  recommend.mutate({
                    type: "item",
                    external_id: id,
                    top_k: topK,
                    filters: buildFilters(filters),
                    include_raw_data: withDetails,
                  })
                }
              />
            </TabsContent>

            <TabsContent value="profile" className="space-y-3">
              {config.searchable_fields.map((field) => (
                <div key={field} className="space-y-1">
                  <Label
                    htmlFor={`profile-${field}`}
                    className="font-mono text-xs"
                  >
                    {field}
                  </Label>
                  <Textarea
                    id={`profile-${field}`}
                    value={profile[field] ?? ""}
                    onChange={(e) =>
                      setProfile({ ...profile, [field]: e.target.value })
                    }
                    rows={2}
                    className="text-sm"
                  />
                </div>
              ))}
              <p className="text-xs text-muted-foreground">
                Describe what you are looking for in terms of your item fields,
                e.g. a candidate&apos;s skills to find matching{" "}
                {config.item_label}s.
              </p>
            </TabsContent>
          </Tabs>

          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <Label>Results (top_k)</Label>
              <span className="font-mono text-sm">{topK}</span>
            </div>
            <Slider
              min={1}
              max={20}
              step={1}
              value={[topK]}
              onValueChange={([v]) => setTopK(v)}
              aria-label="top_k"
            />
          </div>

          <details
            className="group rounded-md border p-3"
            open={config.filter_fields.length <= 3}
          >
            <summary className="cursor-pointer select-none text-sm font-medium">
              Filters
            </summary>
            <div className="mt-3">
              <FilterInputs
                fields={config.filter_fields}
                values={filters}
                onChange={setFilters}
              />
            </div>
          </details>

          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={withDetails}
              onCheckedChange={(v) => setWithDetails(v === true)}
            />
            Show item details
            <span className="text-xs text-muted-foreground">
              (skips the cache)
            </span>
          </label>

          <Button
            className="w-full"
            onClick={run}
            disabled={!current || recommend.isPending}
          >
            {recommend.isPending ? (
              <Loader2 className="animate-spin" />
            ) : (
              <Sparkles />
            )}
            Get recommendations
          </Button>
        </CardContent>
      </Card>

      <section aria-live="polite" className="min-w-0 space-y-4">
        {recommend.isPending ? (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 6 }, (_, i) => (
              <ResultCardSkeleton key={i} />
            ))}
          </div>
        ) : recommend.isError ? (
          <EmptyState
            icon={Search}
            title="The query failed"
            description={apiErrorMessage(recommend.error)}
            action={
              <Button variant="outline" onClick={run} disabled={!current}>
                Try again
              </Button>
            }
          />
        ) : result ? (
          <>
            <ResultsHeader result={result} />
            {result.results.length ? (
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {result.results.map((r) => (
                  <ResultCard
                    key={`${result.query_id}-${r.external_id}`}
                    result={r}
                    queryId={result.query_id}
                    config={config}
                  />
                ))}
              </div>
            ) : (
              <EmptyState
                icon={Search}
                title="No matches"
                description="Nothing matched. Loosen the filters, or upload more items."
              />
            )}
          </>
        ) : (
          <EmptyState
            icon={Sparkles}
            title="Try a recommendation"
            description={`Search by text, find ${config.item_label}s similar to one you have, or match a profile. Results, scores and latency appear here.`}
          />
        )}
      </section>
    </div>
  );
}
