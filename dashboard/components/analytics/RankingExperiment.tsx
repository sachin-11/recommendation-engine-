"use client";

import Link from "next/link";
import { FlaskConical } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ErrorBar,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { CHART_COLORS } from "@/components/analytics/RecommendationChart";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/misc";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useRankingExperiment } from "@/lib/hooks/recommend";
import { formatNumber } from "@/lib/utils";
import type { Rate, RankingExperiment as Experiment, RankingVariant } from "@/types";

// Fixed per variant, whatever the order or count the API returns.
const VARIANTS: Record<RankingVariant, { label: string; short: string; color: string }> = {
  control: { label: "Control (similarity)", short: "Control", color: CHART_COLORS[0] },
  reranked: { label: "Reranked (feedback)", short: "Reranked", color: CHART_COLORS[1] },
};

const SIGNIFICANT = 0.05;

function pct(value: number | null | undefined, digits = 1) {
  return value == null ? "—" : `${(value * 100).toFixed(digits)}%`;
}

function interval(rate: Rate) {
  return rate.low == null || rate.high == null ? "" : `${pct(rate.low)}–${pct(rate.high)}`;
}

function Verdict({ lift, p, metric }: { lift: number | null; p: number | null; metric: string }) {
  if (lift == null || p == null) {
    return <p className="text-sm text-muted-foreground">Not enough {metric} data in both variants yet.</p>;
  }
  const better = lift >= 0;
  return (
    <p className="text-sm">
      <span className="text-2xl font-semibold tabular-nums">
        {better ? "+" : ""}
        {pct(lift, 0)}
      </span>{" "}
      <span className="text-muted-foreground">
        {metric} for reranked ·{" "}
        {p < SIGNIFICANT ? (
          <strong className="text-foreground">significant</strong>
        ) : (
          "not significant yet"
        )}{" "}
        (p = {p < 0.001 ? "< 0.001" : p.toFixed(3)})
      </span>
    </p>
  );
}

type Row = {
  variant: RankingVariant;
  label: string;
  rate: number;
  error: [number, number];
  stats: Experiment["variants"][number];
};

function ExperimentTooltip({ active, payload }: { active?: boolean; payload?: { payload: Row }[] }) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  return (
    <div className="rounded-lg border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md">
      <p className="font-medium">{VARIANTS[row.variant].label}</p>
      <p>
        Engagement {pct(row.stats.engagement_rate.rate)}{" "}
        <span className="text-muted-foreground">({interval(row.stats.engagement_rate)})</span>
      </p>
      <p className="text-muted-foreground">
        {formatNumber(row.stats.engagement)} of {formatNumber(row.stats.impressions)} impressions
      </p>
    </div>
  );
}

function EngagementChart({ variants }: { variants: Experiment["variants"] }) {
  const rows: Row[] = variants
    .filter((v) => v.engagement_rate.rate != null)
    .map((v) => {
      const { rate, low, high } = v.engagement_rate as { rate: number; low: number; high: number };
      // The value sits in the axis label: at the bar end it would collide with the whisker.
      const label = `${VARIANTS[v.variant].short} · ${pct(rate)}`;
      return { variant: v.variant, label, rate, error: [rate - low, high - rate], stats: v };
    });
  // Round the axis up to the next 5% so the ticks are round numbers.
  const top = Math.min(1, Math.ceil(Math.max(...rows.map((r) => r.rate + r.error[1]), 0.01) * 20) / 20);
  const ticks = Array.from({ length: Math.round(top * 20) + 1 }, (_, i) => i / 20);
  return (
    <ResponsiveContainer width="100%" height={48 + rows.length * 56}>
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 24, left: 8, bottom: 4 }} barCategoryGap={14}>
        <CartesianGrid horizontal={false} stroke="hsl(var(--border))" strokeDasharray="3 3" />
        <XAxis
          type="number"
          domain={[0, top]}
          ticks={ticks.length <= 11 ? ticks : undefined}
          tickFormatter={(v: number) => pct(v, 0)}
          stroke="hsl(var(--muted-foreground))"
          fontSize={11}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          type="category"
          dataKey="label"
          width={120}
          stroke="hsl(var(--muted-foreground))"
          fontSize={12}
          tickLine={false}
          axisLine={false}
        />
        <Tooltip content={<ExperimentTooltip />} cursor={{ fill: "hsl(var(--muted))", opacity: 0.5 }} />
        <Bar dataKey="rate" radius={[0, 4, 4, 0]} isAnimationActive={false}>
          {rows.map((r) => (
            <Cell key={r.variant} fill={VARIANTS[r.variant].color} />
          ))}
          <ErrorBar dataKey="error" direction="x" width={8} strokeWidth={2} stroke="hsl(var(--foreground))" />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function RankingExperiment({ days }: { days: number }) {
  const { data, isLoading } = useRankingExperiment(days);

  let body: React.ReactNode;
  if (isLoading || !data) {
    body = <Skeleton className="h-[260px] w-full" />;
  } else {
    const byVariant = Object.fromEntries(data.variants.map((v) => [v.variant, v]));
    const running = data.control_share > 0 || (byVariant.control?.impressions ?? 0) > 0;
    if (!running) {
      body = (
        <EmptyState
          icon={FlaskConical}
          title="No A/B test running"
          description="Set a control share under Settings → Ranking to serve part of your traffic by similarity alone and compare."
          action={
            <Button asChild variant="outline">
              <Link href="/dashboard/settings">Open settings</Link>
            </Button>
          }
        />
      );
    } else {
      const { comparison } = data;
      body = (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2">
            <Verdict lift={comparison.engagement_lift} p={comparison.engagement_p_value} metric="engagement" />
            <Verdict lift={comparison.conversion_lift} p={comparison.conversion_p_value} metric="conversion" />
          </div>
          <div>
            <p className="mb-1 text-sm font-medium">Engagement rate per impression</p>
            <p className="mb-2 text-xs text-muted-foreground">Whiskers show the 95% confidence interval.</p>
            <EngagementChart variants={data.variants} />
          </div>
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>Variant</TableHead>
                <TableHead className="text-right">Queries</TableHead>
                <TableHead className="text-right">Impressions</TableHead>
                <TableHead className="text-right">Engagement</TableHead>
                <TableHead className="text-right">Conversion</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.variants.map((v) => (
                <TableRow key={v.variant}>
                  <TableCell>
                    <span className="inline-flex items-center gap-2">
                      <span
                        className="h-2.5 w-2.5 rounded-sm"
                        style={{ background: VARIANTS[v.variant].color }}
                        aria-hidden
                      />
                      {VARIANTS[v.variant].label}
                    </span>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{formatNumber(v.queries)}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatNumber(v.impressions)}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {pct(v.engagement_rate.rate)}{" "}
                    <span className="text-xs text-muted-foreground">{interval(v.engagement_rate)}</span>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {pct(v.conversion_rate.rate)}{" "}
                    <span className="text-xs text-muted-foreground">{interval(v.conversion_rate)}</span>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      );
    }
  }

  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle className="text-base">Ranking experiment</CardTitle>
        <CardDescription>
          Feedback ranking against similarity alone, last {days} days
          {data && data.control_share > 0 ? ` · ${pct(data.control_share, 0)} of traffic in control` : ""}
        </CardDescription>
      </CardHeader>
      <CardContent>{body}</CardContent>
    </Card>
  );
}
