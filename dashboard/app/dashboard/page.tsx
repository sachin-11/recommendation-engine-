"use client";

import Link from "next/link";
import { ArrowRight, BookOpen, Database, Gauge, KeyRound, Sparkles, TrendingUp, Upload } from "lucide-react";

import { OverviewCards, type Stat } from "@/components/analytics/OverviewCards";
import { RecommendationChart } from "@/components/analytics/RecommendationChart";
import { ItemTable, ItemTableSkeleton } from "@/components/items/ItemTable";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, PageHeader } from "@/components/ui/misc";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiKeys, useMe } from "@/lib/hooks/account";
import { useItems } from "@/lib/hooks/items";
import { useAnalytics, useUsage } from "@/lib/hooks/recommend";
import { DOCS_URL, formatNumber } from "@/lib/utils";
import type { ApiKey } from "@/types";

function isUsable(key: ApiKey) {
  return key.is_active && (!key.expires_at || new Date(key.expires_at) > new Date());
}

export default function DashboardHome() {
  const { data: me } = useMe();
  const overview = useAnalytics();
  const usage = useUsage(30);
  const apiKeys = useApiKeys();
  const recent = useItems({}, 1);

  const breakdown = overview.data?.embedding_status_breakdown;
  const inFlight = (breakdown?.PENDING ?? 0) + (breakdown?.PROCESSING ?? 0);
  const stats: Stat[] = [
    {
      label: "Total items",
      value: formatNumber(overview.data?.total_items),
      icon: Database,
      hint: breakdown && (
        <div className="flex flex-wrap gap-1">
          <Badge variant="success">{formatNumber(breakdown.DONE)} embedded</Badge>
          {inFlight > 0 && <Badge variant="info">{inFlight} in progress</Badge>}
          {breakdown.FAILED > 0 && <Badge variant="destructive">{breakdown.FAILED} failed</Badge>}
        </div>
      ),
    },
    {
      label: "Recommendations today",
      value: formatNumber(overview.data?.total_recommendations_today),
      icon: TrendingUp,
      hint: `${formatNumber(overview.data?.total_recommendations_this_month)} this month`,
    },
    {
      label: "Avg latency",
      value: overview.data?.avg_latency_ms != null ? `${overview.data.avg_latency_ms} ms` : "—",
      icon: Gauge,
      hint: "This month, including cache hits",
    },
    {
      label: "Active API keys",
      value: formatNumber(apiKeys.data?.filter(isUsable).length),
      icon: KeyRound,
      hint: apiKeys.data && `${apiKeys.data.length - apiKeys.data.filter(isUsable).length} revoked or expired`,
    },
  ];

  const hasQueries = (usage.data?.total_recommendations ?? 0) > 0;
  const actions = [
    { href: "/dashboard/items?upload=csv", label: "Upload items", icon: Upload, description: "JSON or CSV" },
    { href: "/dashboard/recommend", label: "Test a recommendation", icon: Sparkles, description: "Live playground" },
  ];

  return (
    <>
      <PageHeader
        title={me ? `Welcome, ${me.name}` : "Overview"}
        description="How your recommendation engine is doing."
      />
      <OverviewCards stats={stats} loading={overview.isLoading || apiKeys.isLoading} />

      <Card className="mt-6">
        <CardHeader>
          <CardTitle className="text-base">Recommendations per day</CardTitle>
          <CardDescription>Last 30 days (UTC)</CardDescription>
        </CardHeader>
        <CardContent>
          {usage.isLoading ? (
            <Skeleton className="h-[260px] w-full" />
          ) : hasQueries && usage.data ? (
            <RecommendationChart daily={usage.data.daily} />
          ) : (
            <EmptyState
              icon={TrendingUp}
              title="No recommendations yet"
              description="Run a query in the playground or call the API, and daily volume shows up here."
              action={
                <Button asChild>
                  <Link href="/dashboard/recommend">
                    <Sparkles /> Open playground
                  </Link>
                </Button>
              }
            />
          )}
        </CardContent>
      </Card>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div className="space-y-1.5">
              <CardTitle className="text-base">Recent items</CardTitle>
              <CardDescription>The last 5 uploaded</CardDescription>
            </div>
            <Button variant="ghost" size="sm" asChild>
              <Link href="/dashboard/items">
                View all <ArrowRight />
              </Link>
            </Button>
          </CardHeader>
          <CardContent className="px-0 pb-2">
            {recent.isLoading ? (
              <ItemTableSkeleton rows={5} selectable={false} />
            ) : recent.data?.items.length ? (
              <ItemTable items={recent.data.items.slice(0, 5)} compact />
            ) : (
              <div className="px-5 pb-3">
                <EmptyState
                  icon={Database}
                  title="No items uploaded yet"
                  description="Upload your first items to start getting recommendations."
                  action={
                    <Button asChild>
                      <Link href="/dashboard/items?upload=json">
                        <Upload /> Upload items
                      </Link>
                    </Button>
                  }
                />
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Quick actions</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {actions.map(({ href, label, icon: Icon, description }) => (
              <Link
                key={href}
                href={href}
                className="flex items-center gap-3 rounded-md border p-3 transition-colors hover:border-primary/50 hover:bg-accent"
              >
                <span className="flex h-9 w-9 items-center justify-center rounded-md bg-primary/10 text-primary">
                  <Icon className="h-4 w-4" />
                </span>
                <span className="flex-1">
                  <span className="block text-sm font-medium">{label}</span>
                  <span className="block text-xs text-muted-foreground">{description}</span>
                </span>
                <ArrowRight className="h-4 w-4 text-muted-foreground" />
              </Link>
            ))}
            <a
              href={DOCS_URL}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-3 rounded-md border p-3 transition-colors hover:border-primary/50 hover:bg-accent"
            >
              <span className="flex h-9 w-9 items-center justify-center rounded-md bg-primary/10 text-primary">
                <BookOpen className="h-4 w-4" />
              </span>
              <span className="flex-1">
                <span className="block text-sm font-medium">View API docs</span>
                <span className="block text-xs text-muted-foreground">Interactive Swagger UI</span>
              </span>
              <ArrowRight className="h-4 w-4 text-muted-foreground" />
            </a>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
