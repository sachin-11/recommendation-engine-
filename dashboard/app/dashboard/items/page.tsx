"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Braces, ChevronLeft, ChevronRight, Database, FileSpreadsheet, Search, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { BatchProgress } from "@/components/items/BatchProgress";
import { CSVUploader } from "@/components/items/CSVUploader";
import { ItemTable, ItemTableSkeleton } from "@/components/items/ItemTable";
import { UploadModal } from "@/components/items/UploadModal";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input, NativeSelect } from "@/components/ui/input";
import { EmptyState, PageHeader } from "@/components/ui/misc";
import { toastApiError } from "@/lib/api";
import { useMe } from "@/lib/hooks/account";
import { useDeleteItems, useItems } from "@/lib/hooks/items";
import { formatNumber } from "@/lib/utils";
import type { EmbeddingStatus } from "@/types";

const STATUSES: EmbeddingStatus[] = ["PENDING", "PROCESSING", "DONE", "FAILED"];

function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

function ItemsPage() {
  const router = useRouter();
  const params = useSearchParams();
  const { data: me } = useMe();
  const [searchInput, setSearchInput] = React.useState("");
  const search = useDebounced(searchInput);
  const [status, setStatus] = React.useState<EmbeddingStatus | "">("");
  const [page, setPage] = React.useState(1);
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  const [batchId, setBatchId] = React.useState<string | null>(null);
  const upload = params.get("upload");
  const setUpload = (kind: "json" | "csv" | null) =>
    router.replace(kind ? `/dashboard/items?upload=${kind}` : "/dashboard/items", { scroll: false });

  const { data, isLoading, isFetching } = useItems({ status, search }, page);
  const remove = useDeleteItems();

  // New filters start again from page 1 with nothing selected.
  React.useEffect(() => {
    setPage(1);
    setSelected(new Set());
  }, [status, search]);

  const deleteSelected = () =>
    remove.mutate(Array.from(selected), {
      onSuccess: ({ deleted }) => {
        toast.success(`Deleted ${deleted} item${deleted === 1 ? "" : "s"}`);
        setSelected(new Set());
        setConfirmDelete(false);
      },
      onError: (error) => toastApiError(error, "Delete failed"),
    });

  const filtered = Boolean(status || search);
  const pages = data?.pages ?? 0;

  return (
    <>
      <PageHeader
        title="Items"
        description={me ? `Your ${me.domain_config.item_label}s and their embedding status.` : undefined}
        actions={
          <>
            <Button variant="outline" onClick={() => setUpload("json")} disabled={!me}>
              <Braces /> Upload JSON
            </Button>
            <Button onClick={() => setUpload("csv")} disabled={!me}>
              <FileSpreadsheet /> Upload CSV
            </Button>
          </>
        }
      />

      {batchId && <BatchProgress batchId={batchId} onDismiss={() => setBatchId(null)} />}

      <Card>
        <div className="flex flex-col gap-3 border-b p-4 sm:flex-row sm:items-center">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search by external_id"
              className="pl-9"
              aria-label="Search by external_id"
            />
          </div>
          <NativeSelect
            value={status}
            onChange={(e) => setStatus(e.target.value as EmbeddingStatus | "")}
            className="sm:w-44"
            aria-label="Filter by status"
          >
            <option value="">All statuses</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s.charAt(0) + s.slice(1).toLowerCase()}
              </option>
            ))}
          </NativeSelect>
          {selected.size > 0 && (
            <Button variant="destructive" onClick={() => setConfirmDelete(true)}>
              <Trash2 /> Delete {selected.size}
            </Button>
          )}
        </div>

        {isLoading ? (
          <ItemTableSkeleton />
        ) : data && data.items.length > 0 ? (
          <>
            <ItemTable items={data.items} selected={selected} onSelectedChange={setSelected} />
            <div className="flex items-center justify-between gap-3 border-t p-4 text-sm text-muted-foreground">
              <span>
                {formatNumber(data.total)} item{data.total === 1 ? "" : "s"}
                {isFetching && " · refreshing…"}
              </span>
              <div className="flex items-center gap-2">
                <span>
                  Page {data.page} of {Math.max(pages, 1)}
                </span>
                <Button
                  variant="outline"
                  size="icon"
                  onClick={() => setPage((p) => p - 1)}
                  disabled={page <= 1}
                  aria-label="Previous page"
                >
                  <ChevronLeft />
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  onClick={() => setPage((p) => p + 1)}
                  disabled={page >= pages}
                  aria-label="Next page"
                >
                  <ChevronRight />
                </Button>
              </div>
            </div>
          </>
        ) : (
          <div className="p-4">
            {filtered ? (
              <EmptyState
                icon={Search}
                title="No matching items"
                description="Try another search or status filter."
                action={
                  <Button
                    variant="outline"
                    onClick={() => {
                      setSearchInput("");
                      setStatus("");
                    }}
                  >
                    Clear filters
                  </Button>
                }
              />
            ) : (
              <EmptyState
                icon={Database}
                title="No items uploaded yet"
                description="Upload your catalogue as JSON or CSV. Each item is embedded and becomes recommendable within seconds."
                action={
                  <div className="flex gap-2">
                    <Button variant="outline" onClick={() => setUpload("json")}>
                      <Braces /> Upload JSON
                    </Button>
                    <Button onClick={() => setUpload("csv")}>
                      <FileSpreadsheet /> Upload CSV
                    </Button>
                  </div>
                }
              />
            )}
          </div>
        )}
      </Card>

      {me && (
        <>
          <UploadModal
            open={upload === "json"}
            onOpenChange={(open) => setUpload(open ? "json" : null)}
            tenant={me}
            onQueued={setBatchId}
          />
          <CSVUploader
            open={upload === "csv"}
            onOpenChange={(open) => setUpload(open ? "csv" : null)}
            tenant={me}
            onQueued={setBatchId}
          />
        </>
      )}

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={`Delete ${selected.size} item${selected.size === 1 ? "" : "s"}?`}
        description="They are removed from the database and from your vector index. This cannot be undone."
        confirmLabel="Delete"
        destructive
        pending={remove.isPending}
        onConfirm={deleteSelected}
      />
    </>
  );
}

export default function Page() {
  return (
    <React.Suspense fallback={<ItemTableSkeleton />}>
      <ItemsPage />
    </React.Suspense>
  );
}
