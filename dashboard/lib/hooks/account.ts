"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { clearApiKey, setApiKey } from "@/lib/session";
import type {
  ApiKey,
  ApiKeyCreated,
  DomainConfig,
  DomainType,
  LoginResponse,
  MessageResponse,
  RegisterResponse,
  Tenant,
} from "@/types";

export const keys = {
  me: ["me"] as const,
  apiKeys: ["api-keys"] as const,
};

export function useMe(enabled = true) {
  return useQuery({
    queryKey: keys.me,
    queryFn: async () => (await api.get<Tenant>("/me")).data,
    enabled,
    staleTime: 60_000,
    retry: false,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (values: { email: string; password: string }) =>
      (await api.post<LoginResponse>("/auth/login", values)).data,
    onSuccess: (data) => {
      setApiKey(data.api_key);
      queryClient.clear();
      queryClient.setQueryData(keys.me, data.tenant);
    },
  });
}

/** Sign in with an existing API key (for tenants created through the API, without a password). */
export function useApiKeyLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (apiKey: string) =>
      (await api.get<Tenant>("/me", { headers: { "X-API-Key": apiKey } })).data,
    onSuccess: (tenant, apiKey) => {
      setApiKey(apiKey);
      queryClient.clear();
      queryClient.setQueryData(keys.me, tenant);
    },
  });
}

export interface RegisterValues {
  name: string;
  email: string;
  password: string;
  domain_type: DomainType;
  domain_config: DomainConfig;
}

/** Create the tenant; the response signs it in with a dashboard session key. */
export function useRegister() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (values: RegisterValues) =>
      (await api.post<RegisterResponse>("/auth/register", values)).data,
    onSuccess: (data) => {
      setApiKey(data.api_key);
      queryClient.clear();
      queryClient.setQueryData(keys.me, data.tenant);
    },
  });
}

/** Confirm the email with the token from the link. Works signed in or not. */
export function useVerifyEmail() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (token: string) => (await api.post<Tenant>("/auth/verify-email", { token })).data,
    onSuccess: (tenant) => {
      const me = queryClient.getQueryData<Tenant>(keys.me);
      if (me?.id === tenant.id) queryClient.setQueryData(keys.me, tenant);
    },
  });
}

export function useResendVerification() {
  return useMutation({
    mutationFn: async () => (await api.post<MessageResponse>("/auth/resend-verification")).data,
  });
}

export function useForgotPassword() {
  return useMutation({
    mutationFn: async (email: string) =>
      (await api.post<MessageResponse>("/auth/forgot-password", { email })).data,
  });
}

export function useResetPassword() {
  return useMutation({
    mutationFn: async (values: { token: string; password: string }) =>
      (await api.post<MessageResponse>("/auth/reset-password", values)).data,
    onSuccess: () => clearApiKey(),
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      try {
        await api.post("/auth/logout");
      } catch {
        // Signing out locally is enough if the server call fails.
      }
    },
    onSettled: () => {
      clearApiKey();
      queryClient.clear();
    },
  });
}

export function useApiKeys() {
  return useQuery({
    queryKey: keys.apiKeys,
    queryFn: async () => (await api.get<ApiKey[]>("/me/api-keys")).data,
  });
}

export function useCreateApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (name: string) => (await api.post<ApiKeyCreated>("/me/api-keys", { name })).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.apiKeys }),
  });
}

export function useRevokeApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await api.delete(`/me/api-keys/${id}`);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.apiKeys }),
  });
}

export function useUpdateDomainConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (domain_config: DomainConfig) =>
      (
        await api.put<{ tenant: Tenant; rebuild_recommended: boolean }>("/me/domain-config", {
          domain_config,
        })
      ).data,
    onSuccess: (data) => queryClient.setQueryData(keys.me, data.tenant),
  });
}

export function useDeleteAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (values: { confirm_email: string; password?: string }) => {
      await api.post("/me/delete", values);
    },
    onSuccess: () => {
      clearApiKey();
      queryClient.clear();
    },
  });
}
