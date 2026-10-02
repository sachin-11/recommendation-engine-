// Mirrors the FastAPI response schemas (app/schemas/*.py).

export type DomainType = "HR" | "FOOD" | "ECOMMERCE" | "EDTECH" | "CUSTOM";
export type EmbeddingStatus = "PENDING" | "PROCESSING" | "DONE" | "FAILED";
export type BatchStatus = "PENDING" | "PROCESSING" | "DONE" | "PARTIAL_FAIL";
export type QueryType = "TEXT" | "ITEM_ID" | "PROFILE" | "ASK";
export type FeedbackType = "CLICK" | "THUMBS_UP" | "THUMBS_DOWN" | "PURCHASE" | "APPLY" | "IGNORE";
export type CacheStatus = "HIT" | "MISS" | "BYPASS" | "PARTIAL";
export type Role = "VIEWER" | "DEVELOPER" | "ADMIN" | "OWNER";

/** Weights (0–5) for how feedback reorders results; similarity counts 1. */
export interface RankingConfig {
  enabled?: boolean;
  engagement?: number;
  conversion?: number;
  negative?: number;
  popularity?: number;
  /** 0–1: how far a query with a user_id leans toward items that user liked. */
  personalization?: number;
  /** 0–1: hybrid search, how much exact keyword matches lift an item. 0 = vector only. */
  keyword?: number;
  /** An OpenAI chat model reorders the top results and explains each. */
  llm_rerank?: boolean;
  /** 2–20: how many top results the model reads. */
  llm_candidates?: number;
  /** 0–1: A/B test share of traffic ordered by similarity alone. 0 runs no test. */
  control_share?: number;
}

export interface DomainConfig {
  primary_embedding_field: string;
  searchable_fields: string[];
  filter_fields: string[];
  item_label: string;
  /** Always present in API responses; optional when sending (defaults apply). */
  ranking?: RankingConfig;
}

export interface User {
  id: string;
  email: string;
  name: string;
  role: Role;
  email_verified: boolean;
  has_password: boolean;
  last_login_at: string | null;
  created_at: string;
  /** Can open the platform admin area (every workspace). */
  is_platform_admin: boolean;
}

/** The workspace (`/me`), plus who is asking. */
export interface Tenant {
  id: string;
  name: string;
  email: string;
  domain_type: DomainType;
  domain_config: DomainConfig;
  is_active: boolean;
  /** The caller's role; integration API keys act as DEVELOPER. */
  role: Role;
  /** The signed-in person; null when signed in with an integration API key. */
  user: User | null;
  has_password: boolean;
  /** False until the emailed link is opened; API keys need a verified email. */
  email_verified: boolean;
  created_at: string;
  updated_at: string;
}

export interface Invitation {
  id: string;
  email: string;
  role: Role;
  invited_by_id: string | null;
  expires_at: string;
  created_at: string;
}

export interface Team {
  members: User[];
  invitations: Invitation[];
}

export interface InvitationInfo {
  workspace_name: string;
  email: string;
  role: Role;
  invited_by: string | null;
  expires_at: string;
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
  created_by_id: string | null;
}

export interface ApiKeyCreated extends ApiKey {
  api_key: string;
  warning: string;
}

/** Sign-up signs the tenant in: `api_key` is a 7-day dashboard session key. */
export interface RegisterResponse {
  tenant: Tenant;
  api_key: string;
  expires_at: string;
  verification_required: boolean;
}

export interface MessageResponse {
  message: string;
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
  /** Why it fits the query; present when LLM re-ranking ordered the results. */
  reason?: string;
}

export interface RecommendResponse {
  results: Recommendation[];
  total: number;
  query_id: string;
  latency_ms: number;
  /** OpenAI tokens used to embed the query; 0 on cache hits and by-item queries. */
  embedding_tokens: number;
  /** OpenAI chat tokens used by LLM re-ranking; 0 when off or cached. */
  rerank_tokens?: number;
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

export interface TokenUsage {
  days: number;
  since: string;
  model: string;
  total_tokens: number;
  by_source: { INGEST: number; QUERY: number };
  api_calls: number;
  texts_embedded: number;
  cache_hits: number;
  price_per_million_tokens: number;
  estimated_cost_usd: number;
  daily: { date: string; ingest_tokens: number; query_tokens: number }[];
}

export interface FeedbackSummary {
  since: string;
  days: number;
  total: number;
  by_type: Record<FeedbackType, number>;
}

export type RankingVariant = "control" | "reranked";

export interface Rate {
  rate: number | null;
  /** 95% confidence interval. */
  low: number | null;
  high: number | null;
}

export interface VariantStats {
  variant: RankingVariant;
  queries: number;
  impressions: number;
  engagement: number;
  conversions: number;
  negatives: number;
  engagement_rate: Rate;
  conversion_rate: Rate;
}

export interface RankingExperiment {
  since: string;
  days: number;
  control_share: number;
  variants: VariantStats[];
  comparison: {
    engagement_lift: number | null;
    engagement_p_value: number | null;
    conversion_lift: number | null;
    conversion_p_value: number | null;
  };
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

// --- Platform admin (/admin) ---

export type WorkspaceStatus = "active" | "suspended";
export type WorkspaceSort = "newest" | "name" | "queries" | "tokens" | "items" | "last_active";

export interface WorkspaceLimits {
  /** Null: no cap. */
  max_items: number | null;
  /** Null: no cap. */
  monthly_query_limit: number | null;
  /** Null: the platform default. */
  rate_limit_rpm: number | null;
}

export interface WorkspaceBilling {
  /** The plan in force now. */
  plan: Plan;
  /** The plan paid for, which may have lapsed. */
  subscribed_plan: Plan;
  subscription_status: string | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  /** The customer in Stripe's dashboard; null before a first checkout. */
  stripe_customer_url: string | null;
  /** Pro given by a platform admin, in force now. */
  complimentary: boolean;
  complimentary_since: string | null;
  /** Null with complimentary: no end. */
  complimentary_until: string | null;
  /** Internal note, never shown to the workspace. */
  complimentary_reason: string | null;
}

export interface WorkspaceSummary {
  id: string;
  name: string;
  email: string;
  domain_type: DomainType;
  status: WorkspaceStatus;
  created_at: string;
  owner_name: string | null;
  members: number;
  items: number;
  queries_this_month: number;
  tokens_this_month: number;
  estimated_cost_this_month_usd: number;
  last_active_at: string | null;
  limits: WorkspaceLimits;
  billing: WorkspaceBilling;
}

export interface WorkspaceList {
  workspaces: WorkspaceSummary[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface WorkspaceDetail extends WorkspaceSummary {
  suspended_at: string | null;
  /** Internal note, never shown to the workspace. */
  suspended_reason: string | null;
  email_verified: boolean;
  embedding_status: Record<EmbeddingStatus, number>;
  queries_daily: { date: string; count: number }[];
  tokens_last_30_days: number;
  member_list: User[];
  api_keys: ApiKey[];
}

export interface PlatformOverview {
  workspaces_total: number;
  workspaces_active: number;
  workspaces_suspended: number;
  workspaces_new_last_30_days: number;
  users_total: number;
  items_total: number;
  queries_today: number;
  queries_this_month: number;
  tokens_this_month: number;
  estimated_cost_this_month_usd: number;
  /** Workspaces whose Pro subscription is in force. */
  pro_paying: number;
  /** Workspaces with complimentary Pro now. */
  pro_complimentary: number;
  /** Subscriptions whose last payment failed. */
  payments_past_due: number;
  queries_daily: { date: string; count: number }[];
  top_workspaces: { id: string; name: string; queries_this_month: number; tokens_this_month: number }[];
}

// --- Offline evaluation ---

export interface EvalQuery {
  id: string;
  query: string;
  /** external_id -> grade: 1 relevant, 2 very relevant, 3 perfect. */
  relevant: Record<string, number>;
  created_at: string;
  updated_at: string;
}

export interface EvalMetrics {
  ndcg: number;
  recall: number;
  mrr: number;
}

export interface EvalVariantResult extends EvalMetrics {
  name: string;
  ranking: Required<RankingConfig>;
  /** Per query, without the result cache. */
  latency_ms_avg: number;
  latency_ms_p95: number;
  rerank_tokens: number;
  rerank_cost_usd: number;
  /** Queries whose LLM answer came from its cache. */
  llm_cached: number;
  /** Queries where the LLM stage failed or timed out. */
  llm_fallbacks: number;
}

export interface EvalQueryResult {
  id: string;
  query: string;
  relevant: Record<string, number>;
  by_variant: Record<string, EvalMetrics>;
  top: Record<string, string[]>;
}

export interface EvalRun {
  k: number;
  queries: number;
  variants: EvalVariantResult[];
  per_query: EvalQueryResult[];
}

// --- Asking in plain language ---

export interface AskInterpretation {
  search_text: string;
  /** Filters read from the question, in the API's filter form. */
  filters: Record<string, unknown>;
  /** Constraints that could not be applied, e.g. "location: no items with 'Hyderabad'". */
  ignored: string[];
  /** Set when the question could not be read; it was then searched as written. */
  fallback?: string;
}

export interface AskResults extends RecommendResponse {
  /** The filters matched nothing, so these results are without them. */
  relaxed: boolean;
  understand_tokens: number;
}

// --- Billing ---

export type Plan = "FREE" | "PRO";

export interface Allowance {
  /** null: no cap. */
  max_items: number | null;
  monthly_queries: number | null;
  rate_limit_rpm: number;
  llm_features: boolean;
}

export interface Billing {
  /** False: billing is off on this server and nothing is limited by plan. */
  enabled: boolean;
  /** The plan in force now. */
  plan: Plan;
  /** The plan paid for, which may have lapsed. */
  subscribed_plan: Plan;
  subscription_status: string | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  /** Pro given by the platform, without a subscription. */
  complimentary: boolean;
  /** When complimentary Pro ends; null with complimentary: no end. */
  complimentary_until: string | null;
  allowance: Allowance;
  usage: { items: number; queries_this_month: number };
  plans: { plan: Plan; allowance: Allowance }[];
  pro_prices: { interval: "month" | "year"; unit_amount: number; currency: string }[];
}
