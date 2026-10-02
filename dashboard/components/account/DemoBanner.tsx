"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useLogout, useMe } from "@/lib/hooks/account";

/** Shown across the dashboard to visitors of the public, read-only demo. */
export function DemoBanner() {
  const { data: me } = useMe();
  const logout = useLogout();
  const router = useRouter();
  if (!me?.is_demo) return null;

  const createOwn = () => logout.mutate(undefined, { onSettled: () => router.push("/register") });

  return (
    <div
      role="status"
      className="mb-6 flex flex-col gap-3 rounded-lg border border-primary/30 bg-primary/5 p-4 text-sm sm:flex-row sm:items-center"
    >
      <Sparkles className="h-5 w-5 shrink-0 text-primary" />
      <p className="flex-1">
        <strong>You are exploring a live, read-only demo</strong> with {me.domain_config.item_label}s already
        embedded. Try{" "}
        <Link href="/dashboard/recommend" className="font-medium text-primary hover:underline">
          Ask
        </Link>{" "}
        with a question like &ldquo;remote AI job with LLMs, 2 to 5 years&rdquo;, then look at Analytics.
      </p>
      <Button size="sm" onClick={createOwn} disabled={logout.isPending}>
        Create your own workspace
      </Button>
    </div>
  );
}
