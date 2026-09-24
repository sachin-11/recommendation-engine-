"use client";

import * as React from "react";
import Link from "next/link";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import { toast } from "sonner";

import { itemHref } from "@/components/items/ItemTable";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toastApiError } from "@/lib/api";
import { useFeedback } from "@/lib/hooks/recommend";
import { cn } from "@/lib/utils";
import type { DomainConfig, FeedbackType, Recommendation } from "@/types";

const LABEL_STYLES: Record<string, { bar: string; text: string }> = {
  "Excellent Match": { bar: "bg-success", text: "text-success" },
  "Good Match": { bar: "bg-primary", text: "text-primary" },
  "Fair Match": { bar: "bg-warning", text: "text-warning" },
  "Weak Match": { bar: "bg-muted-foreground/50", text: "text-muted-foreground" },
};

function formatValue(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "boolean") return value ? "yes" : "no";
  return String(value);
}

/** A short title for the card, from the item's own fields when raw data was requested. */
function titleFor(result: Recommendation, config: DomainConfig): string | null {
  const data = result.raw_data;
  if (!data) return null;
  for (const field of ["title", "name", ...config.searchable_fields]) {
    const value = data[field];
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
}

export function ResultCard({
  result,
  queryId,
  config,
}: {
  result: Recommendation;
  queryId: string;
  config: DomainConfig;
}) {
  const [sent, setSent] = React.useState<FeedbackType | null>(null);
  const feedback = useFeedback();
  const style = LABEL_STYLES[result.score_label] ?? LABEL_STYLES["Weak Match"];
  const percent = Math.max(0, Math.min(100, Math.round(result.score * 100)));
  const title = titleFor(result, config);
  const description = result.raw_data?.[config.primary_embedding_field];

  const send = (type: FeedbackType) =>
    feedback.mutate(
      { query_id: queryId, external_item_id: result.external_id, feedback_type: type },
      {
        onSuccess: () => {
          setSent(type);
          toast.success("Thanks, feedback recorded");
        },
        onError: (error) => toastApiError(error, "Could not send feedback"),
      },
    );

  return (
    <Card className="flex flex-col">
      <CardContent className="flex flex-1 flex-col gap-3 p-4">
        <div className="flex items-start gap-3">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-sm font-semibold text-primary">
            #{result.rank}
          </span>
          <div className="min-w-0 flex-1">
            <Link
              href={itemHref(result.external_id)}
              className="block truncate font-mono text-sm font-medium hover:text-primary hover:underline"
            >
              {result.external_id}
            </Link>
            {title && <p className="truncate text-sm">{title}</p>}
          </div>
        </div>

        {typeof description === "string" && title !== description && (
          <p className="line-clamp-2 text-xs text-muted-foreground">{description}</p>
        )}

        <div className="space-y-1">
          <div className="flex items-center justify-between text-xs">
            <span className={cn("font-medium", style.text)}>{result.score_label}</span>
            <span className="font-mono text-muted-foreground">{result.score.toFixed(4)}</span>
          </div>
          <div
            className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
            role="meter"
            aria-valuenow={percent}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Similarity score"
          >
            <div className={cn("h-full rounded-full", style.bar)} style={{ width: `${percent}%` }} />
          </div>
        </div>

        {Object.keys(result.metadata).length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(result.metadata).map(([key, value]) => (
              <Badge key={key} variant="secondary" className="max-w-full font-normal">
                <span className="text-muted-foreground">{key}:</span>
                <span className="truncate">{formatValue(value)}</span>
              </Badge>
            ))}
          </div>
        )}

        <div className="mt-auto flex items-center justify-end gap-1 pt-1">
          <span className="mr-auto text-xs text-muted-foreground">
            {sent ? "Feedback sent" : "Useful?"}
          </span>
          {(["THUMBS_UP", "THUMBS_DOWN"] as const).map((type) => {
            const Icon = type === "THUMBS_UP" ? ThumbsUp : ThumbsDown;
            return (
              <Button
                key={type}
                variant={sent === type ? "secondary" : "ghost"}
                size="icon"
                className={cn("h-8 w-8", sent === type && (type === "THUMBS_UP" ? "text-success" : "text-destructive"))}
                onClick={() => send(type)}
                disabled={feedback.isPending || sent !== null}
                aria-label={type === "THUMBS_UP" ? "Good recommendation" : "Bad recommendation"}
                aria-pressed={sent === type}
              >
                <Icon />
              </Button>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

export function ResultCardSkeleton() {
  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="flex items-center gap-3">
          <Skeleton className="h-8 w-8 rounded-full" />
          <Skeleton className="h-4 w-32" />
        </div>
        <Skeleton className="h-2 w-full" />
        <div className="flex gap-2">
          <Skeleton className="h-5 w-20 rounded-full" />
          <Skeleton className="h-5 w-24 rounded-full" />
        </div>
      </CardContent>
    </Card>
  );
}
