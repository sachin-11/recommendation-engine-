import type { RecoEngineClient } from "../client";
import type { FeedbackSummary, OverviewStats, UsageStats } from "../types";

export class Analytics {
  constructor(private readonly client: RecoEngineClient) {}

  /** Item count, query volume today and this month, average latency, top items. */
  async overview(): Promise<OverviewStats> {
    return (await this.client.request<OverviewStats>({ method: "GET", url: "/analytics/overview" })).data;
  }

  /** Feedback counts by type for the last `days` days (default 30). */
  async feedbackSummary(options: { days?: number } = {}): Promise<FeedbackSummary> {
    const response = await this.client.request<FeedbackSummary>({
      method: "GET",
      url: "/analytics/feedback-summary",
      params: { days: options.days },
    });
    return response.data;
  }

  /** Daily volume, query types and cache hit rate for the last `days` days (default 30). */
  async usage(options: { days?: number } = {}): Promise<UsageStats> {
    const response = await this.client.request<UsageStats>({
      method: "GET",
      url: "/analytics/usage",
      params: { days: options.days },
    });
    return response.data;
  }
}
