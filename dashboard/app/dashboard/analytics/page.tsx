"use client";

import * as React from "react";
import Link from "next/link";
import { BarChart3, Gauge, MessageSquareHeart, Sparkles, TrendingUp, Zap } from "lucide-react";

import { OverviewCards, type Stat } from "@/components/analytics/OverviewCards";
import { FeedbackDonut, QueryTypeChart, RecommendationChart } from "@/components/analytics/RecommendationChart";
import { itemHref } from "@/components/items/ItemTable";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/input";
import { EmptyState, PageHeader } from "@/components/ui/misc";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAnalytics, useFeedbackSummary, useUsage } from "@/lib/hooks/recommend";
import { formatNumber } from "@/lib/utils";

function ChartCard({
  title,
  description,
  loading,
  children,
}: {
  title: string;
  description?: string;
  loading?: boolean;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent>{loading ? <Skeleton className="h-[260px] w-full" /> : children}</CardContent>
    </Card>
  );
}

export default function AnalyticsPage() {
  const [days, setDays] = React.useState(30);
  const usage = useUsage(days);
  const overview = useAnalytics();
  const feedback = useFeedbackSummary(days);

  const hitRate = usage.data?.cache_hit_rate;
  const stats: Stat[] = [
    {
      label: "Total recommendations",
      value: formatNumber(usage.data?.total_recommendations),
      icon: TrendingUp,
      hint: `Last ${days} days`,
    },
    {
      label: "Avg latency",
      value: overview.data?.avg_latency_ms != null ? `${overview.data.avg_latency_ms} ms` : "—",
      icon: Gauge,
      hint: "This month",
    },
    {
      label: "Cache hit rate",
      value: hitRate != null ? `${Math.round(hitRate * 100)}%` : "—",
      icon: Zap,
      hint: "Of cacheable queries",
    },
    {
      label: "Feedback received",
      value: formatNumber(usage.data?.feedback_total),
      icon: MessageSquareHeart,
      hint: `Last ${days} days`,
    },
  ];

  const loading = usage.isLoading;
  const empty = !loading && (usage.data?.total_recommendations ?? 0) === 0;
  const topItems = overview.data?.top_recommended_items ?? [];

  return (
    <>
      <PageHeader
        title="Analytics"
        description="Query volume, latency, caching and what users think of the results."
        actions={
          <NativeSelect value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-36" aria-label="Period">
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </NativeSelect>
        }
      />
      <OverviewCards stats={stats} loading={loading || overview.isLoading} />

      {empty ? (
        <EmptyState
          className="mt-6"
          icon={BarChart3}
          title="No recommendations yet"
          description="Charts appear once your application or the playground starts asking for recommendations."
          action={
            <Button asChild>
              <Link href="/dashboard/recommend">
                <Sparkles /> Open playground
              </Link>
            </Button>
          }
        />
      ) : (
        <>
          <div className="mt-6 grid gap-6 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <ChartCard title="Daily recommendation volume" description={`Last ${days} days (UTC)`} loading={loading}>
                {usage.data && <RecommendationChart daily={usage.data.daily} variant="area" />}
              </ChartCard>
            </div>
            <ChartCard title="Query types" description="How results were requested" loading={loading}>
              {usage.data && <QueryTypeChart byType={usage.data.by_query_type} />}
            </ChartCard>
          </div>

          <div className="mt-6 grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Most recommended items</CardTitle>
                <CardDescription>Top result most often, this month</CardDescription>
              </CardHeader>
              <CardContent className="px-0">
                {overview.isLoading ? (
                  <div className="space-y-2 px-5">
                    {Array.from({ length: 5 }, (_, i) => (
                      <Skeleton key={i} className="h-8 w-full" />
                    ))}
                  </div>
                ) : topItems.length ? (
                  <Table>
                    <TableHeader>
                      <TableRow className="hover:bg-transparent">
                        <TableHead className="pl-5">#</TableHead>
                        <TableHead>External ID</TableHead>
                        <TableHead className="pr-5 text-right">Times recommended</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {topItems.map((item, index) => (
                        <TableRow key={item.external_id}>
                          <TableCell className="pl-5 text-muted-foreground">{index + 1}</TableCell>
                          <TableCell>
                            <Link href={itemHref(item.external_id)} className="font-mono text-xs hover:text-primary hover:underline">
                              {item.external_id}
                            </Link>
                          </TableCell>
                          <TableCell className="pr-5 text-right tabular-nums">{formatNumber(item.count)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                ) : (
                  <p className="px-5 text-sm text-muted-foreground">No results served this month yet.</p>
                )}
              </CardContent>
            </Card>

            <ChartCard title="Feedback" description={`By type, last ${days} days`} loading={feedback.isLoading}>
              {feedback.data && feedback.data.total > 0 ? (
                <FeedbackDonut byType={feedback.data.by_type} />
              ) : (
                <EmptyState
                  icon={MessageSquareHeart}
                  title="No feedback yet"
                  description="Send CLICK, THUMBS_UP or PURCHASE events to /recommend/feedback, or rate results in the playground."
                />
              )}
            </ChartCard>
          </div>
        </>
      )}
    </>
  );
}
