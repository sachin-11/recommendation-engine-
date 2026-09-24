"use client";

import { useMutation, useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type {
  AnalyticsOverview,
  CacheStatus,
  FeedbackSummary,
  FeedbackType,
  RecommendRequest,
  RecommendResponse,
  RecommendResult,
  UsageResponse,
} from "@/types";

const PATHS = { text: "/recommend/by-text", item: "/recommend/by-item", profile: "/recommend/by-profile" };

export function useRecommend() {
  return useMutation({
    mutationFn: async ({ type, ...body }: RecommendRequest): Promise<RecommendResult> => {
      const response = await api.post<RecommendResponse>(PATHS[type], body);
      const cache = (response.headers["x-cache"] as CacheStatus | undefined) ?? null;
      return { ...response.data, cache };
    },
  });
}

export function useFeedback() {
  return useMutation({
    mutationFn: async (values: { query_id: string; external_item_id: string; feedback_type: FeedbackType }) => {
      await api.post("/recommend/feedback", values);
    },
  });
}

const FIVE_MINUTES = 5 * 60_000;

export const analyticsKeys = {
  overview: ["analytics", "overview"] as const,
  usage: (days: number) => ["analytics", "usage", days] as const,
  feedback: (days: number) => ["analytics", "feedback", days] as const,
};

export function useAnalytics() {
  return useQuery({
    queryKey: analyticsKeys.overview,
    queryFn: async () => (await api.get<AnalyticsOverview>("/analytics/overview")).data,
    staleTime: FIVE_MINUTES,
  });
}

export function useUsage(days = 30) {
  return useQuery({
    queryKey: analyticsKeys.usage(days),
    queryFn: async () => (await api.get<UsageResponse>("/analytics/usage", { params: { days } })).data,
    staleTime: FIVE_MINUTES,
  });
}

export function useFeedbackSummary(days = 30) {
  return useQuery({
    queryKey: analyticsKeys.feedback(days),
    queryFn: async () =>
      (await api.get<FeedbackSummary>("/analytics/feedback-summary", { params: { days } })).data,
    staleTime: FIVE_MINUTES,
  });
}
