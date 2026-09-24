# RecoEngine Dashboard

The admin dashboard and self-service onboarding for the recommendation engine: Next.js 14 (App Router), TypeScript, Tailwind CSS, shadcn/ui components (Radix), TanStack Query, React Hook Form + Zod, Recharts and next-themes.

## Run it locally

The dashboard talks to the FastAPI backend in the repository root, so start that first.

```bash
# 1. Backend (from the repository root): API on :8000, plus Postgres, Redis and the worker
docker compose up -d

# 2. Dashboard
cd dashboard
cp .env.example .env.local     # NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm install
npm run dev                    # http://localhost:3000
```

The backend must allow the dashboard's origin. `ALLOWED_ORIGINS` in the backend `.env` already includes `http://localhost:3000`; add your deployed dashboard URL there too.

Production build: `npm run build && npm run start`. Checks: `npm run typecheck` and `npm run lint`.

## How sign-in works

- **Register** (`/register`) creates the tenant with a password and its first API key (shown once, for your application), then signs you in.
- **Sign in** with email and password to get a 7-day *session key*: an API key flagged as a session, hidden from the API key list and revoked on sign-out.
- Tenants created through the API have no password; they can sign in with an existing API key on the **API key** tab.
- The key is kept in `localStorage` and sent as `X-API-Key`. A `401` signs you out and returns you to the login page; `429` and `503` show a toast.

## Pages

| Route | What it does |
|---|---|
| `/register` | 4 steps: account → domain (HR, Food, E-commerce, EdTech, Custom) → config (tag inputs or JSON, with a live example item) → API key with curl, Python and Node.js snippets |
| `/login` | Email and password, or an API key |
| `/dashboard` | Items, today's recommendations, average latency, active keys; 30-day chart; recent items; quick actions |
| `/dashboard/items` | Search, status filter, pagination, bulk delete, JSON upload with validation and preview, CSV upload with column mapping, live batch progress |
| `/dashboard/items/[id]` | Uploaded data, filter metadata, embedding error, "Find similar", delete |
| `/dashboard/recommend` | Playground: by text, by item (with autocomplete) or by profile; top_k, filters, scores, latency, cache status, thumbs up/down feedback |
| `/dashboard/analytics` | Totals, latency, cache hit rate, feedback; daily volume, query types, most recommended items, feedback breakdown |
| `/dashboard/api-keys` | Create (shown once) and revoke keys |
| `/dashboard/settings` | Edit the domain config, rebuild the index, delete all items, delete the account |

Filter inputs in the playground accept `Delhi` (exact), `Delhi, Pune` (any of), `>=3`, `<5` or `3..8` (ranges) and `true`/`false`.

## Layout

```
app/(auth)/            login and register (public)
app/dashboard/         signed-in pages; layout.tsx is the sidebar + top bar and the session guard
components/ui/         shadcn-style primitives (button, card, dialog, tabs, table, …)
components/{layout,items,recommend,analytics,onboarding}/
lib/api.ts             axios instance and interceptors
lib/hooks/             TanStack Query hooks per API area
lib/validators/        Zod schemas (account, domain config, pasted items)
types/index.ts         response types matching the FastAPI schemas
```
