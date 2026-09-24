"use client";

import Link from "next/link";
import { ChevronRight, Sparkles } from "lucide-react";

import { StatusPill } from "@/components/items/StatusPill";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/controls";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDate } from "@/lib/utils";
import type { Item } from "@/types";

export function itemHref(externalId: string) {
  return `/dashboard/items/${encodeURIComponent(externalId)}`;
}

export function ItemTableSkeleton({ rows = 8, selectable = true }: { rows?: number; selectable?: boolean }) {
  return (
    <Table>
      <TableBody>
        {Array.from({ length: rows }, (_, i) => (
          <TableRow key={i}>
            {selectable && (
              <TableCell className="w-10">
                <Skeleton className="h-4 w-4" />
              </TableCell>
            )}
            <TableCell>
              <Skeleton className="h-4 w-40" />
            </TableCell>
            <TableCell>
              <Skeleton className="h-5 w-20 rounded-full" />
            </TableCell>
            <TableCell>
              <Skeleton className="h-4 w-32" />
            </TableCell>
            <TableCell>
              <Skeleton className="ml-auto h-8 w-20" />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function ItemTable({
  items,
  selected,
  onSelectedChange,
  compact = false,
}: {
  items: Item[];
  selected?: Set<string>;
  onSelectedChange?: (selected: Set<string>) => void;
  compact?: boolean;
}) {
  const selectable = Boolean(selected && onSelectedChange);
  const allSelected = selectable && items.length > 0 && items.every((i) => selected!.has(i.external_id));
  const someSelected = selectable && items.some((i) => selected!.has(i.external_id));

  const toggleAll = () => {
    const next = new Set(selected);
    for (const item of items) {
      if (allSelected) next.delete(item.external_id);
      else next.add(item.external_id);
    }
    onSelectedChange!(next);
  };
  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onSelectedChange!(next);
  };

  return (
    <Table>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          {selectable && (
            <TableHead>
              <Checkbox
                checked={allSelected ? true : someSelected ? "indeterminate" : false}
                onCheckedChange={toggleAll}
                aria-label="Select all on this page"
              />
            </TableHead>
          )}
          <TableHead>External ID</TableHead>
          <TableHead>Status</TableHead>
          <TableHead className={compact ? "hidden sm:table-cell" : undefined}>Created</TableHead>
          <TableHead className="text-right">
            <span className="sr-only">Actions</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((item) => {
          const isSelected = selected?.has(item.external_id) ?? false;
          return (
            <TableRow key={item.id} data-state={isSelected ? "selected" : undefined}>
              {selectable && (
                <TableCell>
                  <Checkbox
                    checked={isSelected}
                    onCheckedChange={() => toggle(item.external_id)}
                    aria-label={`Select ${item.external_id}`}
                  />
                </TableCell>
              )}
              <TableCell className="max-w-[16rem]">
                <Link
                  href={itemHref(item.external_id)}
                  className="block truncate font-mono text-xs font-medium hover:text-primary hover:underline"
                >
                  {item.external_id}
                </Link>
                {item.embedding_status === "FAILED" && typeof item.metadata.error === "string" && (
                  <p className="truncate text-xs text-destructive" title={item.metadata.error}>
                    {item.metadata.error}
                  </p>
                )}
              </TableCell>
              <TableCell>
                <StatusPill status={item.embedding_status} />
              </TableCell>
              <TableCell className={compact ? "hidden text-muted-foreground sm:table-cell" : "text-muted-foreground"}>
                {formatDate(item.created_at)}
              </TableCell>
              <TableCell className="text-right">
                <div className="flex justify-end gap-1">
                  {!compact && item.embedding_status === "DONE" && (
                    <Button variant="ghost" size="sm" asChild>
                      <Link
                        href={`/dashboard/recommend?tab=item&id=${encodeURIComponent(item.external_id)}`}
                        title="Find similar items"
                      >
                        <Sparkles /> <span className="hidden md:inline">Similar</span>
                      </Link>
                    </Button>
                  )}
                  <Button variant="ghost" size="icon" asChild>
                    <Link href={itemHref(item.external_id)} aria-label={`Open ${item.external_id}`}>
                      <ChevronRight />
                    </Link>
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
