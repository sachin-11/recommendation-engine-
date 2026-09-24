"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type {
  AsyncUploadResponse,
  BatchStatusResponse,
  CsvUploadResponse,
  EmbeddingStatus,
  IndexStats,
  Item,
  ItemList,
} from "@/types";

export interface ItemFilters {
  status?: EmbeddingStatus | "";
  search?: string;
}

export const itemKeys = {
  all: ["items"] as const,
  list: (filters: ItemFilters, page: number) => ["items", "list", filters, page] as const,
  detail: (externalId: string) => ["items", "detail", externalId] as const,
  batch: (batchId: string) => ["batch", batchId] as const,
  indexStats: ["index-stats"] as const,
};

const IN_FLIGHT: EmbeddingStatus[] = ["PENDING", "PROCESSING"];

/** One page of items. Polls every 5 s while any item on the page is still being embedded. */
export function useItems(filters: ItemFilters, page: number) {
  return useQuery({
    queryKey: itemKeys.list(filters, page),
    queryFn: async () =>
      (
        await api.get<ItemList>("/items", {
          params: {
            page,
            status: filters.status || undefined,
            search: filters.search?.trim() || undefined,
          },
        })
      ).data,
    placeholderData: keepPreviousData,
    refetchInterval: (query) =>
      query.state.data?.items.some((item) => IN_FLIGHT.includes(item.embedding_status)) ? 5000 : false,
  });
}

export function useItem(externalId: string) {
  return useQuery({
    queryKey: itemKeys.detail(externalId),
    queryFn: async () => (await api.get<Item>(`/items/${encodeURIComponent(externalId)}`)).data,
    refetchInterval: (query) =>
      query.state.data && IN_FLIGHT.includes(query.state.data.embedding_status) ? 3000 : false,
    retry: false,
  });
}

/** Batch progress, polled every 3 s until it is DONE or PARTIAL_FAIL. */
export function useBatchStatus(batchId: string | null) {
  return useQuery({
    queryKey: itemKeys.batch(batchId ?? ""),
    queryFn: async () => (await api.get<BatchStatusResponse>(`/items/batch/${batchId}`)).data,
    enabled: Boolean(batchId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "DONE" || status === "PARTIAL_FAIL" ? false : 3000;
    },
  });
}

export function useUploadItems() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (items: Record<string, unknown>[]) =>
      (await api.post<AsyncUploadResponse>("/items/upload", { items, async: true })).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: itemKeys.all }),
  });
}

export function useUploadCsv() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return (
        await api.post<CsvUploadResponse>("/items/upload-csv", form, {
          headers: { "Content-Type": "multipart/form-data" },
        })
      ).data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: itemKeys.all }),
  });
}

export function useDeleteItems() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (externalIds: string[]) =>
      (
        await api.post<{ deleted: number; not_found: string[] }>("/items/bulk-delete", {
          external_ids: externalIds,
        })
      ).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: itemKeys.all }),
  });
}

export function useDeleteAllItems() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => (await api.delete<{ deleted: number }>("/items")).data,
    onSuccess: () => queryClient.invalidateQueries(),
  });
}

export function useRebuildIndex() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => (await api.post<AsyncUploadResponse>("/index/rebuild")).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: itemKeys.all }),
  });
}

export function useIndexStats() {
  return useQuery({
    queryKey: itemKeys.indexStats,
    queryFn: async () => (await api.get<IndexStats>("/index/stats")).data,
    staleTime: 60_000,
    retry: false,
  });
}
