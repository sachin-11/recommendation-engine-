import type { LucideIcon } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export interface Stat {
  label: string;
  value: string;
  icon: LucideIcon;
  hint?: React.ReactNode;
}

export function StatCard({ stat, loading }: { stat: Stat; loading?: boolean }) {
  const Icon = stat.icon;
  return (
    <Card>
      <CardContent className="p-5">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium text-muted-foreground">{stat.label}</p>
          <span className="flex h-8 w-8 items-center justify-center rounded-md bg-primary/10 text-primary">
            <Icon className="h-4 w-4" />
          </span>
        </div>
        {loading ? (
          <>
            <Skeleton className="mt-3 h-8 w-24" />
            <Skeleton className="mt-2 h-4 w-32" />
          </>
        ) : (
          <>
            <p className="mt-2 text-3xl font-semibold tracking-tight tabular-nums">{stat.value}</p>
            {stat.hint && <div className="mt-1 text-xs text-muted-foreground">{stat.hint}</div>}
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function OverviewCards({ stats, loading }: { stats: Stat[]; loading?: boolean }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {stats.map((stat) => (
        <StatCard key={stat.label} stat={stat} loading={loading} />
      ))}
    </div>
  );
}
