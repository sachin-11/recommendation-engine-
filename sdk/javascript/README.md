# @recoengine/sdk

Official JavaScript/TypeScript client for [RecoEngine](https://docs.recoengine.io): upload items, get recommendations by text, similar item or profile, and send feedback.

- Full TypeScript types for every request and response
- Retries `429` and `503` up to 3 times, honouring `Retry-After`, with exponential backoff
- Typed errors: `AuthError`, `NotFoundError`, `ValidationError`, `RateLimitError`, `ServiceUnavailableError`, `ConnectionError`
- ESM and CommonJS builds; Node 18+ and modern browsers; one dependency (axios)

## Install

```bash
npm install @recoengine/sdk
```

## Usage

```ts
import { RecoEngineClient } from "@recoengine/sdk";

const client = new RecoEngineClient({
  apiKey: process.env.RECO_API_KEY!, // reco_…
  // baseUrl: "https://api.recoengine.io", // default
  // timeout: 10000,                       // ms
});

// 1. Upload items (queued; poll the batch)
const batch = await client.items.upload([
  { external_id: "job-101", title: "Senior Python Developer", description: "Build FastAPI services", location: "Remote" },
]);
if (batch.mode === "async") await client.items.waitForBatch(batch.batch_id);

// 2. Recommend
const { results, query_id, cache } = await client.recommend.byText("python backend developer", {
  topK: 5,
  filters: { location: ["Remote", "Delhi"] },
});
for (const r of results) console.log(r.rank, r.external_id, r.score_label);

// 3. Feedback
await client.recommend.submitFeedback(query_id, results[0].external_id, "CLICK");
```

CommonJS works too: `const { RecoEngineClient } = require("@recoengine/sdk");`

## Methods

| Method | Returns |
|---|---|
| `items.upload(items, { async? })` | `UploadResult` (a batch, or per-item results with `async: false`) |
| `items.uploadCSV(pathOrBlob, filename?)` | `CsvBatchResult` |
| `items.getBatchStatus(batchId)` / `items.waitForBatch(batchId, { intervalMs?, timeoutMs? })` | `BatchStatus` |
| `items.get(externalId)` / `items.list({ page?, status?, search? })` | `Item` / `PaginatedItems` |
| `items.delete(externalId)` / `items.deleteMany(ids)` | `void` / `{ deleted, not_found }` |
| `recommend.byText(query, options?)` | `RecommendResult` |
| `recommend.byItem(externalId, options?)` | `RecommendResult` |
| `recommend.byProfile(profile, options?)` | `RecommendResult` |
| `recommend.batch(queries, { topK? })` | `BatchRecommendResult` |
| `recommend.submitFeedback(queryId, itemId, type)` | `void` |
| `analytics.overview()` / `analytics.feedbackSummary({ days? })` / `analytics.usage({ days? })` / `analytics.tokens({ days? })` | stats |

`options` is `{ topK?, filters?, includeRawData? }`. Filters: `"Delhi"` exact, `["Delhi", "Pune"]` any of, `{ gte: 3, lte: 8 }` range.

## Errors

```ts
import { RateLimitError, ValidationError } from "@recoengine/sdk";

try {
  await client.recommend.byText("…", { filters: { salary: 5 } });
} catch (e) {
  if (e instanceof ValidationError) console.error(e.message, e.details); // unknown filter field
  else if (e instanceof RateLimitError) console.error(`retry in ${e.retryAfter}s`);
  else throw e;
}
```

Every error has `status`, `code`, `details` and `requestId` (quote it when contacting support).

## Retries

`429` and `503` are retried up to `maxRetries` (default 3) times. The wait is the `Retry-After` header when present, otherwise `retryBaseDelayMs × 2^attempt` with jitter, never more than `maxRetryDelayMs` (default 30 s). Other errors are not retried.

## Development

```bash
npm install
npm test          # jest
npm run typecheck
npm run build     # dist/index.js (ESM), dist/index.cjs (CJS), .d.ts
```
