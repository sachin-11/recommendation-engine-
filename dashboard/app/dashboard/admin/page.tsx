"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Activity,
  Building2,
  ChevronLeft,
  ChevronRight,
  Coins,
  Database,
  Search,
  ShieldCheck,
} from "lucide-react";

import { OverviewCards, type Stat } from "@/components/analytics/OverviewCards";
import { RecommendationChart } from "@/components/analytics/RecommendationChart";
import { NotPlatformAdmin, WorkspaceStatusBadge } from "@/components/admin/WorkspaceStatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, NativeSelect } from "@/components/ui/input";
import { EmptyState, PageHeader } from "@/components/ui/misc";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useMe } from "@/lib/hooks/account";
import { usePlatformOverview, useWorkspaces } from "@/lib/hooks/admin";
import { formatCost, formatNumber, formatRelative } from "@/lib/utils";
import type { WorkspaceSort, WorkspaceStatus } from "@/types";

const SORTS: { value: WorkspaceSort; label: string }[] = [
  { value: "newest", label: "Newest first" },
  { value: "last_active", label: "Recently active" },
  { value: "queries", label: "Most queries" },
  { value: "tokens", label: "Most tokens" },
  { value: "items", label: "Most items" },
  { value: "name", label: "Name" },
];

function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

function PlatformStats() {
  const { data, isLoading } = usePlatformOverview();
  const stats: Stat[] = [
    {
      label: "Workspaces",
      value: formatNumber(data?.workspaces_total),
      icon: Building2,
      hint: data && (
        <>
          {formatNumber(data.workspaces_active)} active · {formatNumber(data.workspaces_suspended)} suspended ·{" "}
          {formatNumber(data.workspaces_new_last_30_days)} new in 30 days
        </>
      ),
    },
    {
      label: "Recommendations this month",
      value: formatNumber(data?.queries_this_month),
      icon: Activity,
      hint: data && <>{formatNumber(data.queries_today)} today</>,
    },
    {
      label: "Items",
      value: formatNumber(data?.items_total),
      icon: Database,
      hint: data && <>{formatNumber(data.users_total)} users</>,
    },
    {
      label: "OpenAI cost this month",
      value: data ? formatCost(data.estimated_cost_this_month_usd) : "—",
      icon: Coins,
      hint: data && <>{formatNumber(data.tokens_this_month)} embedding tokens</>,
    },
  ];

  return (
    <div className="space-y-6">
      <OverviewCards stats={stats} loading={isLoading} />
      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-base">Recommendations, all workspaces</CardTitle>
            <CardDescription>Last 30 days (UTC)</CardDescription>
          </CardHeader>
          <CardContent>
            {data ? (
              <RecommendationChart
                variant="area"
                daily={data.queries_daily.map((d) => ({ ...d, avg_latency_ms: null }))}
              />
            ) : (
              <Skeleton className="h-[260px] w-full" />
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Busiest this month</CardTitle>
            <CardDescription>By recommendations served</CardDescription>
          </CardHeader>
          <CardContent>
            {!data ? (
              <Skeleton className="h-40 w-full" />
            ) : data.top_workspaces.length ? (
              <ol className="space-y-3">
                {data.top_workspaces.map((w, i) => (
                  <li key={w.id} className="flex items-center justify-between gap-3 text-sm">
                    <Link href={`/dashboard/admin/workspaces/${w.id}`} className="truncate hover:underline">
                      <span className="mr-2 text-muted-foreground">{i + 1}.</span>
                      {w.name}
                    </Link>
                    <span className="shrink-0 tabular-nums text-muted-foreground">
                      {formatNumber(w.queries_this_month)}
                    </span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-sm text-muted-foreground">No activity yet this month.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function WorkspaceTable() {
  const router = useRouter();
  const [searchInput, setSearchInput] = React.useState("");
  const search = useDebounced(searchInput);
  const [status, setStatus] = React.useState<WorkspaceStatus | "">("");
  const [sort, setSort] = React.useState<WorkspaceSort>("newest");
  const [page, setPage] = React.useState(1);
  React.useEffect(() => setPage(1), [search, status, sort]);

  const { data, isLoading, isFetching } = useWorkspaces({ search, status, sort, page });
  const pages = data?.pages ?? 0;

  return (
    <Card>
      <div className="flex flex-col gap-3 border-b p-4 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search by name or email"
            className="pl-9"
            aria-label="Search workspaces"
          />
        </div>
        <NativeSelect
          value={status}
          onChange={(e) => setStatus(e.target.value as WorkspaceStatus | "")}
          className="sm:w-36"
          aria-label="Filter by status"
        >
          <option value="">All statuses</option>
          <option value="active">Active</option>
          <option value="suspended">Suspended</option>
        </NativeSelect>
        <NativeSelect
          value={sort}
          onChange={(e) => setSort(e.target.value as WorkspaceSort)}
          className="sm:w-44"
          aria-label="Sort"
        >
          {SORTS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </NativeSelect>
      </div>

      {isLoading || !data ? (
        <div className="space-y-3 p-5">
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : data.workspaces.length ? (
        <>
          <Table className={isFetching ? "opacity-60 transition-opacity" : undefined}>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>Workspace</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="hidden text-right md:table-cell">Items</TableHead>
                <TableHead className="text-right">Queries (month)</TableHead>
                <TableHead className="hidden text-right lg:table-cell">Cost (month)</TableHead>
                <TableHead className="hidden xl:table-cell">Last active</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.workspaces.map((w) => (
                <TableRow
                  key={w.id}
                  className="cursor-pointer"
                  onClick={() => router.push(`/dashboard/admin/workspaces/${w.id}`)}
                >
                  <TableCell>
                    <Link
                      href={`/dashboard/admin/workspaces/${w.id}`}
                      className="font-medium hover:underline"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {w.name}
                    </Link>
                    <p className="text-xs text-muted-foreground">
                      {w.email} · {w.domain_type} · {w.members} member{w.members === 1 ? "" : "s"}
                    </p>
                  </TableCell>
                  <TableCell>
                    <WorkspaceStatusBadge status={w.status} />
                  </TableCell>
                  <TableCell className="hidden text-right tabular-nums md:table-cell">
                    {formatNumber(w.items)}
                    {w.limits.max_items !== null && (
                      <span className="text-muted-foreground"> / {formatNumber(w.limits.max_items)}</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatNumber(w.queries_this_month)}
                    {w.limits.monthly_query_limit !== null && (
                      <span className="text-muted-foreground"> / {formatNumber(w.limits.monthly_query_limit)}</span>
                    )}
                  </TableCell>
                  <TableCell className="hidden text-right tabular-nums lg:table-cell">
                    {formatCost(w.estimated_cost_this_month_usd)}
                  </TableCell>
                  <TableCell className="hidden text-muted-foreground xl:table-cell" title={w.last_active_at ?? undefined}>
                    {formatRelative(w.last_active_at)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <div className="flex items-center justify-between border-t px-4 py-3 text-sm text-muted-foreground">
            <span>
              {formatNumber(data.total)} workspace{data.total === 1 ? "" : "s"} · Page {data.page} of {Math.max(pages, 1)}
            </span>
            <div className="flex gap-1">
              <Button
                variant="outline"
                size="icon"
                onClick={() => setPage((p) => p - 1)}
                disabled={page <= 1}
                aria-label="Previous page"
              >
                <ChevronLeft />
              </Button>
              <Button
                variant="outline"
                size="icon"
                onClick={() => setPage((p) => p + 1)}
                disabled={page >= pages}
                aria-label="Next page"
              >
                <ChevronRight />
              </Button>
            </div>
          </div>
        </>
      ) : (
        <div className="p-5">
          <EmptyState icon={Search} title="No workspaces match" description="Try another search or status." />
        </div>
      )}
    </Card>
  );
}

export default function AdminPage() {
  const { data: me, isLoading } = useMe();
  if (isLoading) return null;
  return (
    <>
      <PageHeader
        title="Platform admin"
        description="Every workspace on the platform: usage, cost, suspension and limits."
      />
      {me?.user?.is_platform_admin ? (
        <div className="space-y-6">
          <PlatformStats />
          <div className="flex items-center gap-2 pt-2">
            <ShieldCheck className="h-4 w-4 text-primary" />
            <h2 className="text-base font-semibold">Workspaces</h2>
          </div>
          <WorkspaceTable />
        </div>
      ) : (
        <NotPlatformAdmin />
      )}
    </>
  );
}
