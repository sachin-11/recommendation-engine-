import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import MockAdapter from "axios-mock-adapter";

import {
  AuthError,
  ConnectionError,
  NotFoundError,
  RateLimitError,
  RecoEngineClient,
  RecoEngineError,
  ServiceUnavailableError,
  ValidationError,
} from "../src";

const RESULTS = {
  results: [
    { rank: 1, external_id: "job-101", score: 0.81, score_label: "Good Match", metadata: { location: "Remote" } },
  ],
  total: 1,
  query_id: "54c4f208-3af7-4790-acdb-1b246e04a27f",
  latency_ms: 120,
  request_id: "rid-1",
};

function setup(options: Partial<ConstructorParameters<typeof RecoEngineClient>[0]> = {}) {
  const sleeps: number[] = [];
  const client = new RecoEngineClient({
    apiKey: "reco_test_key",
    baseUrl: "http://api.test/",
    sleep: async (ms) => {
      sleeps.push(ms);
    },
    ...options,
  });
  // The axios instance is private; tests swap its transport for a mock.
  const mock = new MockAdapter((client as unknown as { http: import("axios").AxiosInstance }).http);
  return { client, mock, sleeps };
}

const error = (code: string, message: string, details: unknown[] = []) => ({ error: { code, message, details } });

describe("RecoEngineClient", () => {
  it("requires an API key", () => {
    expect(() => new RecoEngineClient({ apiKey: "" })).toThrow(RecoEngineError);
  });

  it("sends the key and uses /api/v1 on the base URL", async () => {
    const { client, mock } = setup();
    mock.onGet("/me").reply(200, {});
    mock.onGet("/analytics/overview").reply((config) => {
      expect(config.baseURL).toBe("http://api.test/api/v1");
      expect(config.headers?.["X-API-Key"]).toBe("reco_test_key");
      return [200, { total_items: 3 }];
    });
    await expect(client.analytics.overview()).resolves.toEqual({ total_items: 3 });
  });

  it("fetches token usage for a period", async () => {
    const { client, mock } = setup();
    mock.onGet("/analytics/tokens").reply((config) => {
      expect(config.params).toEqual({ days: 7 });
      return [200, { days: 7, total_tokens: 1234, by_source: { INGEST: 1000, QUERY: 234 } }];
    });
    const usage = await client.analytics.tokens({ days: 7 });
    expect(usage.total_tokens).toBe(1234);
    expect(usage.by_source.QUERY).toBe(234);
  });
});

describe("items", () => {
  it("uploads asynchronously by default", async () => {
    const { client, mock } = setup();
    mock.onPost("/items/upload").reply((config) => {
      expect(JSON.parse(config.data)).toEqual({ items: [{ external_id: "job-1", title: "Dev" }], async: true });
      return [202, { mode: "async", batch_id: "b1", status: "PENDING" }];
    });
    const result = await client.items.upload([{ external_id: "job-1", title: "Dev" }]);
    expect(result.mode).toBe("async");
  });

  it("uploads synchronously when asked", async () => {
    const { client, mock } = setup();
    mock.onPost("/items/upload").reply((config) => {
      expect(JSON.parse(config.data).async).toBe(false);
      return [200, { mode: "sync", succeeded: 1, failed: 0, total_items: 1, results: [] }];
    });
    const result = await client.items.upload([{ external_id: 1 }], { async: false });
    expect(result.mode === "sync" && result.succeeded).toBe(1);
  });

  it("uploads a CSV file from a path as multipart", async () => {
    const { client, mock } = setup();
    const dir = mkdtempSync(join(tmpdir(), "reco-"));
    const path = join(dir, "jobs.csv");
    writeFileSync(path, "id,description\nj1,Python role\n");
    mock.onPost("/items/upload-csv").reply(async (config) => {
      expect(config.data).toBeInstanceOf(FormData);
      const file = (config.data as FormData).get("file") as File;
      expect(file.name).toBe("jobs.csv");
      expect(await file.text()).toContain("Python role");
      return [202, { mode: "async", batch_id: "b2", column_mapping: { id: "external_id" } }];
    });
    const result = await client.items.uploadCSV(path);
    expect(result.column_mapping).toEqual({ id: "external_id" });
  });

  it("lists with filters and encodes ids in paths", async () => {
    const { client, mock } = setup();
    mock.onGet("/items").reply((config) => {
      expect(config.params).toEqual({ page: 2, status: "FAILED", search: "job" });
      return [200, { items: [], total: 0, page: 2, page_size: 20, pages: 0 }];
    });
    mock.onDelete("/items/a%2Fb").reply(204);
    await client.items.list({ page: 2, status: "FAILED", search: "job" });
    await client.items.delete("a/b");
    expect(mock.history.delete).toHaveLength(1);
  });

  it("waits for a batch to finish", async () => {
    const { client, mock } = setup();
    mock
      .onGet("/items/batch/b1")
      .replyOnce(200, { status: "PROCESSING", processed_items: 1, total_items: 2 })
      .onGet("/items/batch/b1")
      .replyOnce(200, { status: "DONE", processed_items: 2, total_items: 2 });
    const batch = await client.items.waitForBatch("b1", { intervalMs: 1 });
    expect(batch.status).toBe("DONE");
  });
});

describe("recommend", () => {
  it("maps options to the API body and exposes X-Cache", async () => {
    const { client, mock } = setup();
    mock.onPost("/recommend/by-text").reply((config) => {
      expect(JSON.parse(config.data)).toEqual({
        query: "python developer",
        top_k: 5,
        filters: { location: "Remote" },
        include_raw_data: false,
      });
      return [200, RESULTS, { "x-cache": "HIT" }];
    });
    const result = await client.recommend.byText("python developer", { topK: 5, filters: { location: "Remote" } });
    expect(result.results[0]?.external_id).toBe("job-101");
    expect(result.cache).toBe("HIT");
  });

  it("supports by-item, by-profile and feedback", async () => {
    const { client, mock } = setup();
    mock.onPost("/recommend/by-item").reply(200, RESULTS);
    mock.onPost("/recommend/by-profile").reply((config) => {
      expect(JSON.parse(config.data).profile).toEqual({ skills: "Python" });
      return [200, RESULTS];
    });
    mock.onPost("/recommend/feedback").reply((config) => {
      expect(JSON.parse(config.data)).toEqual({
        query_id: RESULTS.query_id,
        external_item_id: "job-101",
        feedback_type: "CLICK",
      });
      return [201, {}];
    });
    expect((await client.recommend.byItem("job-7")).cache).toBeNull();
    await client.recommend.byProfile({ skills: "Python" }, { includeRawData: true });
    await expect(client.recommend.submitFeedback(RESULTS.query_id, "job-101", "CLICK")).resolves.toBeUndefined();
  });
});

describe("retries and errors", () => {
  it("retries 503 with exponential backoff, then succeeds", async () => {
    const { client, mock, sleeps } = setup({ retryBaseDelayMs: 100 });
    mock.onPost("/recommend/by-text").replyOnce(503, error("service_unavailable", "down"));
    mock.onPost("/recommend/by-text").replyOnce(503, error("service_unavailable", "down"));
    mock.onPost("/recommend/by-text").replyOnce(200, RESULTS);
    await expect(client.recommend.byText("q")).resolves.toMatchObject({ total: 1 });
    expect(sleeps).toHaveLength(2);
    expect(sleeps[0]).toBeGreaterThanOrEqual(50);
    expect(sleeps[0]).toBeLessThanOrEqual(100);
    expect(sleeps[1]).toBeGreaterThanOrEqual(100);
    expect(sleeps[1]).toBeLessThanOrEqual(200);
  });

  it("honours Retry-After on 429, capped by maxRetryDelayMs", async () => {
    const { client, mock, sleeps } = setup({ maxRetryDelayMs: 5000 });
    mock.onGet("/analytics/overview").replyOnce(429, error("rate_limited", "slow down"), { "retry-after": "2" });
    mock.onGet("/analytics/overview").replyOnce(429, error("rate_limited", "slow down"), { "retry-after": "3600" });
    mock.onGet("/analytics/overview").replyOnce(200, { total_items: 0 });
    await client.analytics.overview();
    expect(sleeps).toEqual([2000, 5000]);
  });

  it("gives up after 3 retries with a typed error", async () => {
    const { client, mock, sleeps } = setup();
    mock.onGet("/analytics/overview").reply(429, error("rate_limited", "Rate limit exceeded"), {
      "retry-after": "7",
      "x-request-id": "rid-429",
    });
    const failure = await client.analytics.overview().catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(RateLimitError);
    expect(failure).toMatchObject({ status: 429, code: "rate_limited", retryAfter: 7, requestId: "rid-429" });
    expect(sleeps).toHaveLength(3);
    expect(mock.history.get).toHaveLength(4);
  });

  it.each([
    [401, AuthError],
    [403, AuthError],
    [404, NotFoundError],
    [400, ValidationError],
    [409, ValidationError],
    [422, ValidationError],
    [500, RecoEngineError],
  ])("maps %i without retrying", async (status, ErrorClass) => {
    const { client, mock, sleeps } = setup();
    mock.onGet("/items/x").reply(status, error("some_code", "Nope", [{ field: "x", message: "bad" }]));
    const failure = await client.items.get("x").catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(ErrorClass);
    expect(failure).toMatchObject({ status, message: "Nope", details: [{ field: "x", message: "bad" }] });
    expect(sleeps).toHaveLength(0);
  });

  it("raises ServiceUnavailableError after retrying 503", async () => {
    const { client, mock } = setup({ maxRetries: 1 });
    mock.onPost("/recommend/by-text").reply(503, error("service_unavailable", "Pinecone timed out"));
    await expect(client.recommend.byText("q")).rejects.toBeInstanceOf(ServiceUnavailableError);
    expect(mock.history.post).toHaveLength(2);
  });

  it("raises ConnectionError when the API cannot be reached", async () => {
    const { client, mock } = setup();
    mock.onGet("/analytics/overview").networkError();
    await expect(client.analytics.overview()).rejects.toBeInstanceOf(ConnectionError);
  });
});
