// Request options use camelCase; response objects keep the API's snake_case field names,
// so they match the API reference exactly.

export type EmbeddingStatus = "PENDING" | "PROCESSING" | "DONE" | "FAILED";
export type BatchState = "PENDING" | "PROCESSING" | "DONE" | "PARTIAL_FAIL";
export type QueryType = "TEXT" | "ITEM_ID" | "PROFILE";
export type FeedbackType = "CLICK" | "THUMBS_UP" | "THUMBS_DOWN" | "PURCHASE" | "APPLY" | "IGNORE";
export type CacheStatus = "HIT" | "MISS" | "BYPASS" | "PARTIAL";

// ---------------------------------------------------------------- client

export interface ClientOptions {
  /** Your API key (`reco_…`). */
  apiKey: string;
  /** API origin. Default: https://api.recoengine.io */
  baseUrl?: string;
  /** Per-request timeout in milliseconds. Default: 10000 */
  timeout?: number;
  /** Retries on 429 and 503 responses. Default: 3 */
  maxRetries?: number;
  /** First backoff delay in ms when no Retry-After header is sent; doubles each retry. Default: 500 */
  retryBaseDelayMs?: number;
  /** Upper bound for any single wait, including Retry-After. Default: 30000 */
  maxRetryDelayMs?: number;
  /** Replaces the wait between retries (tests, custom schedulers). */
  sleep?: (ms: number) => Promise<void>;
}

// ---------------------------------------------------------------- items

/** An item to upload: `external_id` plus any fields your domain config uses. */
export interface ItemInput {
  external_id: string | number;
  [field: string]: unknown;
}

export interface Item {
  id: string;
  external_id: string;
  embedding_status: EmbeddingStatus;
  pinecone_id: string | null;
  batch_id: string | null;
  raw_data: Record<string, unknown>;
  /** Filter fields stored with the vector; `error` explains a FAILED status. */
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface PaginatedItems {
  items: Item[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface ListOptions {
  page?: number;
  status?: EmbeddingStatus;
  /** Part of an external_id, any case. */
  search?: string;
}

export interface UploadOptions {
  /** true (default): queue and return a batch. false: embed up to 50 items before returning. */
  async?: boolean;
}

export interface BatchStatus {
  batch_id: string;
  status: BatchState;
  total_items: number;
  processed_items: number;
  failed_items: number;
  progress_percentage: number;
  created_at: string;
  completed_at: string | null;
}

export interface BatchResult extends BatchStatus {
  mode: "async";
  status_url: string;
}

export interface CsvBatchResult extends BatchResult {
  /** CSV header -> item field it was stored as. */
  column_mapping: Record<string, string>;
}

export interface SyncUploadResult {
  mode: "sync";
  total_items: number;
  succeeded: number;
  failed: number;
  results: { external_id: string; status: EmbeddingStatus; error: string | null }[];
}

export type UploadResult = BatchResult | SyncUploadResult;

export interface WaitOptions {
  /** Poll interval in ms. Default: 2000 */
  intervalMs?: number;
  /** Give up after this many ms. Default: 300000 */
  timeoutMs?: number;
}

// ---------------------------------------------------------------- recommendations

/**
 * Filters on your domain config's `filter_fields`:
 * `"Delhi"` exact, `["Delhi", "Pune"]` any of, `{ gte: 3, lte: 8 }` range
 * (also gt, lt, eq, ne, in, nin).
 */
export type Filters = Record<string, unknown>;

export interface RecommendOptions {
  /** Number of results, 1–100. Default: 10 */
  topK?: number;
  filters?: Filters;
  /** Include each item's uploaded data (these responses are never cached). */
  includeRawData?: boolean;
}

export interface Recommendation {
  rank: number;
  external_id: string;
  /** Cosine similarity, higher is closer. */
  score: number;
  score_label: "Excellent Match" | "Good Match" | "Fair Match" | "Weak Match" | string;
  metadata: Record<string, unknown>;
  raw_data?: Record<string, unknown> | null;
}

export interface RecommendResult {
  results: Recommendation[];
  total: number;
  /** Pass to `submitFeedback`. */
  query_id: string;
  latency_ms: number;
  /** OpenAI tokens used to embed the query; 0 on cache hits and item queries. */
  embedding_tokens: number;
  request_id: string;
  /** Value of the X-Cache response header. */
  cache: CacheStatus | null;
}

export interface BatchQuery {
  id: string;
  query: string;
  filters?: Filters;
}

export interface BatchRecommendResult {
  results: Record<string, Recommendation[]>;
  query_ids: Record<string, string>;
  latency_ms: number;
  /** OpenAI tokens used for the whole batch. */
  embedding_tokens: number;
  request_id: string;
  cache: CacheStatus | null;
}

// ---------------------------------------------------------------- analytics

export interface ItemCount {
  external_id: string;
  count: number;
}

export interface OverviewStats {
  total_items: number;
  total_recommendations_today: number;
  total_recommendations_this_month: number;
  avg_latency_ms: number | null;
  top_queried_items: ItemCount[];
  top_recommended_items: ItemCount[];
  embedding_status_breakdown: Record<EmbeddingStatus, number>;
  period: { today_since: string; month_since: string };
}

export interface FeedbackSummary {
  since: string;
  days: number;
  total: number;
  by_type: Record<FeedbackType, number>;
}

export interface UsageStats {
  days: number;
  since: string;
  total_recommendations: number;
  daily: { date: string; count: number; avg_latency_ms: number | null }[];
  by_query_type: Record<QueryType, number>;
  cache_hit_rate: number | null;
  feedback_total: number;
}

export interface TokenUsage {
  days: number;
  since: string;
  /** Embedding model the tokens were spent on. */
  model: string;
  total_tokens: number;
  /** Tokens for uploads (INGEST) and queries (QUERY). */
  by_source: { INGEST: number; QUERY: number };
  api_calls: number;
  texts_embedded: number;
  /** Texts served from the embedding cache, which cost no tokens. */
  cache_hits: number;
  price_per_million_tokens: number;
  estimated_cost_usd: number;
  daily: { date: string; ingest_tokens: number; query_tokens: number }[];
}

// ---------------------------------------------------------------- errors

export interface ApiErrorDetail {
  field?: string | null;
  message: string;
  type?: string | null;
}
