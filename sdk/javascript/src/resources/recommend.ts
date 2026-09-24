import type { AxiosResponse } from "axios";

import type { RecoEngineClient } from "../client";
import type {
  BatchQuery,
  BatchRecommendResult,
  CacheStatus,
  FeedbackType,
  RecommendOptions,
  RecommendResult,
} from "../types";

function body(options: RecommendOptions) {
  return {
    top_k: options.topK ?? 10,
    filters: options.filters ?? {},
    include_raw_data: options.includeRawData ?? false,
  };
}

function withCache<T>(response: AxiosResponse<T>): T & { cache: CacheStatus | null } {
  const cache = (response.headers?.["x-cache"] as CacheStatus | undefined) ?? null;
  return { ...response.data, cache };
}

export class Recommend {
  constructor(private readonly client: RecoEngineClient) {}

  /** Items most similar to free text. */
  async byText(query: string, options: RecommendOptions = {}): Promise<RecommendResult> {
    return withCache(
      await this.client.request<Omit<RecommendResult, "cache">>({
        method: "POST",
        url: "/recommend/by-text",
        data: { query, ...body(options) },
      }),
    );
  }

  /** Items similar to one of your items; the item itself is never returned. */
  async byItem(externalId: string, options: RecommendOptions = {}): Promise<RecommendResult> {
    return withCache(
      await this.client.request<Omit<RecommendResult, "cache">>({
        method: "POST",
        url: "/recommend/by-item",
        data: { external_id: externalId, ...body(options) },
      }),
    );
  }

  /** Items matching a profile, e.g. `{ skills: "Python", experience: "5 years" }`. */
  async byProfile(profile: Record<string, string>, options: RecommendOptions = {}): Promise<RecommendResult> {
    return withCache(
      await this.client.request<Omit<RecommendResult, "cache">>({
        method: "POST",
        url: "/recommend/by-profile",
        data: { profile, ...body(options) },
      }),
    );
  }

  /** Up to 20 text queries in one request. */
  async batch(queries: BatchQuery[], options: Pick<RecommendOptions, "topK"> = {}): Promise<BatchRecommendResult> {
    return withCache(
      await this.client.request<Omit<BatchRecommendResult, "cache">>({
        method: "POST",
        url: "/recommend/batch",
        data: { queries, top_k: options.topK ?? 10 },
      }),
    );
  }

  /** Record a user's reaction to a result, using the `query_id` of the recommendation. */
  async submitFeedback(queryId: string, itemId: string, type: FeedbackType): Promise<void> {
    await this.client.request({
      method: "POST",
      url: "/recommend/feedback",
      data: { query_id: queryId, external_item_id: itemId, feedback_type: type },
    });
  }
}
