"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { EvalQuery, EvalRun } from "@/types";

export const evaluationKeys = { queries: ["evaluation", "queries"] as const };

export function useEvalQueries() {
  return useQuery({
    queryKey: evaluationKeys.queries,
    queryFn: async () => (await api.get<EvalQuery[]>("/evaluation/queries")).data,
  });
}

function useGoldenSetMutation<TVariables, TData>(mutationFn: (variables: TVariables) => Promise<TData>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: evaluationKeys.queries }),
  });
}

/** Adds a query, or replaces the relevant items of the same query text. */
export function useSaveEvalQuery() {
  return useGoldenSetMutation(
    async (values: { query: string; relevant: Record<string, number> }) =>
      (await api.post<EvalQuery>("/evaluation/queries", values)).data,
  );
}

export function useDeleteEvalQuery() {
  return useGoldenSetMutation(async (id: string) => {
    await api.delete(`/evaluation/queries/${id}`);
  });
}

export function useRunEvaluation() {
  return useMutation({
    // Every golden query runs once per variant; allow for a large set.
    mutationFn: async (k: number) => (await api.post<EvalRun>("/evaluation/run", { k }, { timeout: 120_000 })).data,
  });
}
