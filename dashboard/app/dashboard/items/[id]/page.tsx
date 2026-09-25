"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, SearchX, Sparkles, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { StatusPill } from "@/components/items/StatusPill";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { JsonView } from "@/components/ui/json-view";
import { CopyButton, EmptyState } from "@/components/ui/misc";
import { Skeleton } from "@/components/ui/skeleton";
import { toastApiError } from "@/lib/api";
import { useMe } from "@/lib/hooks/account";
import { useDeleteItems, useItem } from "@/lib/hooks/items";
import { can } from "@/lib/roles";
import { formatDate } from "@/lib/utils";

function safeDecode(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value; // already decoded and contains a literal "%"
  }
}

export default function ItemDetailPage({ params }: { params: { id: string } }) {
  const externalId = safeDecode(params.id);
  const router = useRouter();
  const { data: item, isLoading, isError } = useItem(externalId);
  const remove = useDeleteItems();
  const { data: me } = useMe();
  const [confirm, setConfirm] = React.useState(false);

  const back = (
    <Button variant="ghost" size="sm" asChild className="mb-4 -ml-2">
      <Link href="/dashboard/items">
        <ArrowLeft /> All items
      </Link>
    </Button>
  );

  if (isLoading) {
    return (
      <>
        {back}
        <Skeleton className="mb-6 h-8 w-64" />
        <div className="grid gap-4 lg:grid-cols-3">
          <Skeleton className="h-80 lg:col-span-2" />
          <Skeleton className="h-80" />
        </div>
      </>
    );
  }

  if (isError || !item) {
    return (
      <>
        {back}
        <EmptyState
          icon={SearchX}
          title="Item not found"
          description={`There is no item with external_id "${externalId}". It may have been deleted.`}
          action={
            <Button asChild>
              <Link href="/dashboard/items">Back to items</Link>
            </Button>
          }
        />
      </>
    );
  }

  const error = typeof item.metadata.error === "string" ? item.metadata.error : null;
  const { error: _omit, ...metadata } = item.metadata;
  void _omit;

  return (
    <>
      {back}
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <h1 className="truncate font-mono text-xl font-semibold">{item.external_id}</h1>
          <StatusPill status={item.embedding_status} />
        </div>
        <div className="flex gap-2">
          {item.embedding_status === "DONE" && (
            <Button variant="outline" asChild>
              <Link href={`/dashboard/recommend?tab=item&id=${encodeURIComponent(item.external_id)}`}>
                <Sparkles /> Find similar
              </Link>
            </Button>
          )}
          {can(me, "DEVELOPER") && (
            <Button variant="destructive" onClick={() => setConfirm(true)}>
              <Trash2 /> Delete
            </Button>
          )}
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
          <strong>Embedding failed:</strong> {error}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-base">Uploaded data</CardTitle>
            <CardDescription>Exactly as it was sent.</CardDescription>
          </CardHeader>
          <CardContent>
            <JsonView value={item.raw_data} className="max-h-[32rem]" />
          </CardContent>
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Details</CardTitle>
            </CardHeader>
            <CardContent>
              <dl className="space-y-3 text-sm">
                {[
                  ["Created", formatDate(item.created_at)],
                  ["Updated", formatDate(item.updated_at)],
                  ["Item ID", item.id],
                  ["Vector ID", item.pinecone_id ?? "Not embedded yet"],
                  ["Batch", item.batch_id ?? "—"],
                ].map(([label, value]) => (
                  <div key={label} className="flex items-start justify-between gap-3">
                    <dt className="text-muted-foreground">{label}</dt>
                    <dd className="flex min-w-0 items-center gap-1 text-right font-mono text-xs">
                      <span className="truncate" title={value}>
                        {value}
                      </span>
                      {label === "Item ID" && <CopyButton value={value} className="h-6 w-6" />}
                    </dd>
                  </div>
                ))}
              </dl>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Filter metadata</CardTitle>
              <CardDescription>Stored with the vector; usable as recommendation filters.</CardDescription>
            </CardHeader>
            <CardContent>
              <JsonView value={metadata} />
            </CardContent>
          </Card>
        </div>
      </div>

      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={`Delete ${item.external_id}?`}
        description="The item is removed from the database and from your vector index."
        confirmLabel="Delete"
        destructive
        pending={remove.isPending}
        onConfirm={() =>
          remove.mutate([item.external_id], {
            onSuccess: () => {
              toast.success(`Deleted ${item.external_id}`);
              router.replace("/dashboard/items");
            },
            onError: (err) => toastApiError(err, "Delete failed"),
          })
        }
      />
    </>
  );
}
