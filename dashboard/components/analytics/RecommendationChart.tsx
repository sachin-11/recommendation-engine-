"use client";

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { UsageResponse } from "@/types";

// Chart colours come from CSS variables, so they follow the light/dark theme.
export const CHART_COLORS = [1, 2, 3, 4, 5, 6].map((n) => `hsl(var(--chart-${n}))`);

const axis = { stroke: "hsl(var(--muted-foreground))", fontSize: 11, tickLine: false, axisLine: false };
const grid = { stroke: "hsl(var(--border))", strokeDasharray: "3 3", vertical: false };
const tooltipStyle = {
  contentStyle: {
    background: "hsl(var(--popover))",
    border: "1px solid hsl(var(--border))",
    borderRadius: 8,
    fontSize: 12,
    color: "hsl(var(--popover-foreground))",
  },
  labelStyle: { color: "hsl(var(--muted-foreground))" },
  cursor: { fill: "hsl(var(--muted))", opacity: 0.5 },
};

function shortDate(iso: string) {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
}

type Daily = UsageResponse["daily"];

export function RecommendationChart({ daily, variant = "line" }: { daily: Daily; variant?: "line" | "area" }) {
  const data = daily.map((d) => ({ ...d, label: shortDate(d.date) }));
  const common = { data, margin: { top: 8, right: 8, left: -16, bottom: 0 } };
  return (
    <ResponsiveContainer width="100%" height={260}>
      {variant === "area" ? (
        <AreaChart {...common}>
          <defs>
            <linearGradient id="volume" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={CHART_COLORS[0]} stopOpacity={0.35} />
              <stop offset="100%" stopColor={CHART_COLORS[0]} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid {...grid} />
          <XAxis dataKey="label" {...axis} minTickGap={24} />
          <YAxis {...axis} allowDecimals={false} />
          <Tooltip {...tooltipStyle} formatter={(v) => [v, "Recommendations"]} />
          <Area type="monotone" dataKey="count" stroke={CHART_COLORS[0]} strokeWidth={2} fill="url(#volume)" />
        </AreaChart>
      ) : (
        <LineChart {...common}>
          <CartesianGrid {...grid} />
          <XAxis dataKey="label" {...axis} minTickGap={24} />
          <YAxis {...axis} allowDecimals={false} />
          <Tooltip {...tooltipStyle} formatter={(v) => [v, "Recommendations"]} />
          <Line type="monotone" dataKey="count" stroke={CHART_COLORS[0]} strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
        </LineChart>
      )}
    </ResponsiveContainer>
  );
}

const QUERY_LABELS: Record<string, string> = { TEXT: "By text", ITEM_ID: "By item", PROFILE: "By profile" };

export function QueryTypeChart({ byType }: { byType: UsageResponse["by_query_type"] }) {
  const data = Object.entries(byType).map(([type, count]) => ({ type: QUERY_LABELS[type] ?? type, count }));
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
        <CartesianGrid {...grid} />
        <XAxis dataKey="type" {...axis} />
        <YAxis {...axis} allowDecimals={false} />
        <Tooltip {...tooltipStyle} formatter={(v) => [v, "Queries"]} />
        <Bar dataKey="count" radius={[6, 6, 0, 0]} maxBarSize={56}>
          {data.map((_, i) => (
            <Cell key={i} fill={CHART_COLORS[i]} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

const FEEDBACK_LABELS: Record<string, string> = {
  CLICK: "Clicks",
  THUMBS_UP: "Thumbs up",
  THUMBS_DOWN: "Thumbs down",
  PURCHASE: "Purchases",
  APPLY: "Applications",
  IGNORE: "Ignored",
};

export function FeedbackDonut({ byType }: { byType: Record<string, number> }) {
  const data = Object.entries(byType)
    .map(([type, value], i) => ({ name: FEEDBACK_LABELS[type] ?? type, value, color: CHART_COLORS[i % CHART_COLORS.length] }))
    .filter((d) => d.value > 0);
  return (
    <ResponsiveContainer width="100%" height={260}>
      <PieChart>
        <Tooltip {...tooltipStyle} />
        <Pie data={data} dataKey="value" nameKey="name" innerRadius="55%" outerRadius="80%" paddingAngle={2} strokeWidth={0}>
          {data.map((d) => (
            <Cell key={d.name} fill={d.color} />
          ))}
        </Pie>
        <Legend verticalAlign="bottom" iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
      </PieChart>
    </ResponsiveContainer>
  );
}
