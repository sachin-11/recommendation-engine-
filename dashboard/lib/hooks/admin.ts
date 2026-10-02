"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type {
  PlatformOverview,
  WorkspaceDetail,
  WorkspaceLimits,
  WorkspaceList,
  WorkspaceSort,
  WorkspaceStatus,
} from "@/types";

export const adminKeys = {
  all: ["admin"] as const,
  overview: ["admin", "overview"] as const,
  list: (filters: object) => ["admin", "workspaces", filters] as const,
  detail: (id: string) => ["admin", "workspace", id] as const,
};

export function usePlatformOverview() {
  return useQuery({
    queryKey: adminKeys.overview,
    queryFn: async () => (await api.get<PlatformOverview>("/admin/overview")).data,
  });
}

export interface WorkspaceFilters {
  search: string;
  status: WorkspaceStatus | "";
  sort: WorkspaceSort;
  page: number;
}

export function useWorkspaces({ search, status, sort, page }: WorkspaceFilters) {
  return useQuery({
    queryKey: adminKeys.list({ search, status, sort, page }),
    queryFn: async () =>
      (
        await api.get<WorkspaceList>("/admin/workspaces", {
          params: { search: search || undefined, status: status || undefined, sort, page, page_size: 25 },
        })
      ).data,
    placeholderData: keepPreviousData,
  });
}

export function useWorkspace(id: string) {
  return useQuery({
    queryKey: adminKeys.detail(id),
    queryFn: async () => (await api.get<WorkspaceDetail>(`/admin/workspaces/${id}`)).data,
    retry: false,
  });
}

/** Every change returns the updated workspace; the list and overview are refetched. */
function useWorkspaceMutation<TVariables>(
  request: (variables: TVariables) => Promise<WorkspaceDetail>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: request,
    onSuccess: (workspace) => {
      queryClient.setQueryData(adminKeys.detail(workspace.id), workspace);
      queryClient.invalidateQueries({ queryKey: adminKeys.all });
    },
  });
}

export function useSuspendWorkspace() {
  return useWorkspaceMutation(
    async ({ id, reason }: { id: string; reason: string }) =>
      (await api.post<WorkspaceDetail>(`/admin/workspaces/${id}/suspend`, { reason })).data,
  );
}

export function useActivateWorkspace() {
  return useWorkspaceMutation(
    async (id: string) => (await api.post<WorkspaceDetail>(`/admin/workspaces/${id}/activate`)).data,
  );
}

export function useUpdateLimits() {
  return useWorkspaceMutation(
    async ({ id, limits }: { id: string; limits: WorkspaceLimits }) =>
      (await api.patch<WorkspaceDetail>(`/admin/workspaces/${id}/limits`, limits)).data,
  );
}

export function useGrantComplimentary() {
  return useWorkspaceMutation(
    async ({ id, days, reason }: { id: string; days: number | null; reason: string }) =>
      (await api.put<WorkspaceDetail>(`/admin/workspaces/${id}/complimentary`, { days, reason })).data,
  );
}

export function useRevokeComplimentary() {
  return useWorkspaceMutation(
    async (id: string) => (await api.delete<WorkspaceDetail>(`/admin/workspaces/${id}/complimentary`)).data,
  );
}
