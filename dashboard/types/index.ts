// Mirrors the FastAPI response schemas (app/schemas/*.py).

export type DomainType = "HR" | "FOOD" | "ECOMMERCE" | "EDTECH" | "CUSTOM";
export type EmbeddingStatus = "PENDING" | "PROCESSING" | "DONE" | "FAILED";
export type BatchStatus = "PENDING" | "PROCESSING" | "DONE" | "PARTIAL_FAIL";
export type QueryType = "TEXT" | "ITEM_ID" | "PROFILE";
export type FeedbackType = "CLICK" | "THUMBS_UP" | "THUMBS_DOWN" | "PURCHASE" | "APPLY" | "IGNORE";
export type CacheStatus = "HIT" | "MISS" | "BYPASS" | "PARTIAL";

export interface DomainConfig {
  primary_embedding_field: string;
  searchable_fields: string[];
  filter_fields: string[];
  item_label: string;
}

export interface Tenant {
  id: string;
  name: string;
  email: string;
  domain_type: DomainType;
  domain_config: DomainConfig;
  is_active: boolean;
  has_password: boolean;
  created_at: string;
  updated_at: string;
}

export interface ApiKey {
  id: string;
  name: string;
  key_prefix: string;
  display_key: string;
  is_active: boolean;
  last_used_at: string | null;
  expires_at: string | null;
  created_at: string;
}

export interface ApiKeyCreated extends ApiKey {
  api_key: string;
  warning: string;
}

export interface RegisterResponse {
  tenant: Tenant;
  api_key: string;
  key: ApiKey;
  warning: string;
}

export interface LoginResponse {
  tenant: Tenant;
  api_key: string;
  expires_at: string;
}

export interface Item {
  id: string;
  external_id: string;
  embedding_status: EmbeddingStatus;
  pinecone_id: string | null;
  batch_id: string | null;
  raw_data: Record<string, unknown>;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ItemList {
  items: Item[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface BatchStatusResponse {
  batch_id: string;
  status: BatchStatus;
  total_items: number;
  processed_items: number;
  failed_items: number;
  progress_percentage: number;
  created_at: string;
  completed_at: string | null;
}

export interface AsyncUploadResponse extends BatchStatusResponse {
  mode: "async";
  status_url: string;
}

export interface CsvUploadResponse extends AsyncUploadResponse {
  column_mapping: Record<string, string>;
}

export interface SyncUploadResponse {
  mode: "sync";
  total_items: number;
  succeeded: number;
  failed: number;
  results: { external_id: string; status: EmbeddingStatus; error: string | null }[];
}

export type Filters = Record<string, unknown>;

export interface Recommendation {
  rank: number;
  external_id: string;
  score: number;
  score_label: string;
  metadata: Record<string, unknown>;
  raw_data?: Record<string, unknown> | null;
}

export interface RecommendResponse {
  results: Recommendation[];
  total: number;
  query_id: string;
  latency_ms: number;
  request_id: string;
}

/** A recommendation response plus the X-Cache header. */
export interface RecommendResult extends RecommendResponse {
  cache: CacheStatus | null;
}

export type RecommendRequest =
  | { type: "text"; query: string; top_k: number; filters: Filters; include_raw_data?: boolean }
  | { type: "item"; external_id: string; top_k: number; filters: Filters; include_raw_data?: boolean }
  | {
      type: "profile";
      profile: Record<string, string>;
      top_k: number;
      filters: Filters;
      include_raw_data?: boolean;
    };

export interface ItemCount {
  external_id: string;
  count: number;
}

export interface AnalyticsOverview {
  total_items: number;
  total_recommendations_today: number;
  total_recommendations_this_month: number;
  avg_latency_ms: number | null;
  top_queried_items: ItemCount[];
  top_recommended_items: ItemCount[];
  embedding_status_breakdown: Record<EmbeddingStatus, number>;
  period: { today_since: string; month_since: string };
}

export interface UsageResponse {
  days: number;
  since: string;
  total_recommendations: number;
  daily: { date: string; count: number; avg_latency_ms: number | null }[];
  by_query_type: Record<QueryType, number>;
  cache_hit_rate: number | null;
  feedback_total: number;
}

export interface FeedbackSummary {
  since: string;
  days: number;
  total: number;
  by_type: Record<FeedbackType, number>;
}

export interface IndexStats {
  index_name: string;
  exists: boolean;
  total_vector_count: number;
  dimension: number | null;
  index_fullness: number | null;
  namespaces: Record<string, number>;
  items_by_status: Record<EmbeddingStatus, number>;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: { field?: string | null; message: string; type?: string | null }[];
  };
}
