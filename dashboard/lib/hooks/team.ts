"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { keys as accountKeys } from "@/lib/hooks/account";
import { setApiKey } from "@/lib/session";
import type { Invitation, InvitationInfo, LoginResponse, Role, Team, User } from "@/types";

export const teamKeys = { team: ["team"] as const };

export function useTeam() {
  return useQuery({
    queryKey: teamKeys.team,
    queryFn: async () => (await api.get<Team>("/me/members")).data,
  });
}

function useTeamMutation<TVariables, TData>(mutationFn: (variables: TVariables) => Promise<TData>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: teamKeys.team }),
  });
}

export function useInvite() {
  return useTeamMutation(
    async (values: { email: string; role: Role }) => (await api.post<Invitation>("/me/members/invitations", values)).data,
  );
}

export function useRevokeInvitation() {
  return useTeamMutation(async (id: string) => {
    await api.delete(`/me/members/invitations/${id}`);
  });
}

export function useChangeRole() {
  return useTeamMutation(
    async ({ id, role }: { id: string; role: Role }) => (await api.patch<User>(`/me/members/${id}`, { role })).data,
  );
}

export function useRemoveMember() {
  return useTeamMutation(async (id: string) => {
    await api.delete(`/me/members/${id}`);
  });
}

export function useTransferOwnership() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (values: { user_id: string; password?: string }) =>
      (await api.post<User>("/me/members/transfer-ownership", values)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: teamKeys.team });
      queryClient.invalidateQueries({ queryKey: accountKeys.me });
    },
  });
}

/** Details behind an invitation link (public). */
export function useInvitationInfo(token: string | null) {
  return useQuery({
    queryKey: ["invitation", token],
    queryFn: async () => (await api.post<InvitationInfo>("/auth/invitations/lookup", { token })).data,
    enabled: Boolean(token),
    retry: false,
    staleTime: Infinity,
  });
}

/** Accept an invitation and sign in as the new member. */
export function useAcceptInvitation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (values: { token: string; name: string; password: string }) =>
      (await api.post<LoginResponse>("/auth/invitations/accept", values)).data,
    onSuccess: (data) => {
      setApiKey(data.api_key);
      queryClient.clear();
      queryClient.setQueryData(accountKeys.me, data.tenant);
    },
  });
}
