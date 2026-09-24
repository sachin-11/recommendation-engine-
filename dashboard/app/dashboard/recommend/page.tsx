"use client";

import { Suspense } from "react";

import { QueryPlayground } from "@/components/recommend/QueryPlayground";
import { PageHeader } from "@/components/ui/misc";
import { Skeleton } from "@/components/ui/skeleton";
import { useMe } from "@/lib/hooks/account";

function PlaygroundSkeleton() {
  return (
    <div className="grid gap-6 lg:grid-cols-[22rem_1fr]">
      <Skeleton className="h-[32rem]" />
      <Skeleton className="h-64" />
    </div>
  );
}

export default function RecommendPage() {
  const { data: me } = useMe();
  return (
    <>
      <PageHeader
        title="Recommendation playground"
        description="Run live queries against your index. Every query is logged and counts toward analytics."
      />
      {me ? (
        <Suspense fallback={<PlaygroundSkeleton />}>
          <QueryPlayground tenant={me} />
        </Suspense>
      ) : (
        <PlaygroundSkeleton />
      )}
    </>
  );
}
