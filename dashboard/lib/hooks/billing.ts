"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { Billing } from "@/types";

export const billingKeys = { billing: ["billing"] as const };

export function useBilling() {
  return useQuery({
    queryKey: billingKeys.billing,
    queryFn: async () => (await api.get<Billing>("/billing")).data,
  });
}

/** Opens Stripe Checkout for Pro: the browser leaves for Stripe's page. */
export function useCheckout() {
  return useMutation({
    mutationFn: async (interval: "month" | "year") =>
      (await api.post<{ url: string }>("/billing/checkout", { interval })).data.url,
    onSuccess: (url) => window.location.assign(url),
  });
}

/** Opens the Stripe Customer Portal. */
export function usePortal() {
  return useMutation({
    mutationFn: async () => (await api.post<{ url: string }>("/billing/portal")).data.url,
    onSuccess: (url) => window.location.assign(url),
  });
}

/** Reads the subscription from Stripe now (after checkout, before the webhook lands). */
export function useSyncBilling() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => (await api.post<Billing>("/billing/sync")).data,
    onSuccess: (data) => queryClient.setQueryData(billingKeys.billing, data),
  });
}
