"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { MessageSquareText, Search } from "lucide-react";

import { AskPanel } from "@/components/recommend/AskPanel";
import { QueryPlayground } from "@/components/recommend/QueryPlayground";
import { PageHeader } from "@/components/ui/misc";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useMe } from "@/lib/hooks/account";
import type { Tenant } from "@/types";

function PlaygroundSkeleton() {
  return (
    <div className="grid gap-6 lg:grid-cols-[22rem_1fr]">
      <Skeleton className="h-[32rem]" />
      <Skeleton className="h-64" />
    </div>
  );
}

/** Ask (plain language) or Search (text, item, profile). Links that open a search form
 * (e.g. "similar items" with ?tab=item&id=...) land on Search. */
function PlaygroundTabs({ tenant }: { tenant: Tenant }) {
  const params = useSearchParams();
  return (
    <Tabs defaultValue={params.get("tab") ? "search" : "ask"} className="space-y-6">
      <TabsList>
        <TabsTrigger value="ask">
          <MessageSquareText /> Ask
        </TabsTrigger>
        <TabsTrigger value="search">
          <Search /> Search
        </TabsTrigger>
      </TabsList>
      <TabsContent value="ask">
        <AskPanel tenant={tenant} />
      </TabsContent>
      <TabsContent value="search">
        <QueryPlayground tenant={tenant} />
      </TabsContent>
    </Tabs>
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
          <PlaygroundTabs tenant={me} />
        </Suspense>
      ) : (
        <PlaygroundSkeleton />
      )}
    </>
  );
}
