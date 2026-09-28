"use client";

import * as React from "react";
import Link from "next/link";
import { Activity, ArrowLeft, Ban, Coins, Database, Loader2, PlayCircle, SearchX, Users } from "lucide-react";
import { toast } from "sonner";

import { NotPlatformAdmin, WorkspaceStatusBadge } from "@/components/admin/WorkspaceStatusBadge";
import { OverviewCards, type Stat } from "@/components/analytics/OverviewCards";
import { RecommendationChart } from "@/components/analytics/RecommendationChart";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input, Textarea } from "@/components/ui/input";
import { FieldError, Label } from "@/components/ui/label";
import { EmptyState, PageHeader } from "@/components/ui/misc";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { apiErrorMessage, toastApiError } from "@/lib/api";
import { useMe } from "@/lib/hooks/account";
import {
  useActivateWorkspace,
  useSuspendWorkspace,
  useUpdateLimits,
  useWorkspace,
} from "@/lib/hooks/admin";
import { ROLE_INFO } from "@/lib/roles";
import { formatCost, formatDate, formatNumber, formatRelative } from "@/lib/utils";
import type { EmbeddingStatus, WorkspaceDetail, WorkspaceLimits } from "@/types";

const LIMIT_FIELDS: { key: keyof WorkspaceLimits; label: string; hint: string }[] = [
  { key: "max_items", label: "Max items", hint: "Uploads that would exceed it are refused. Blank: no cap." },
  {
    key: "monthly_query_limit",
    label: "Recommendations per month",
    hint: "Resets on the 1st (UTC). Blank: no cap.",
  },
  { key: "rate_limit_rpm", label: "Requests per minute, per key", hint: "Blank: the platform default." },
];

type LimitDraft = Record<keyof WorkspaceLimits, string>;

function toDraft(limits: WorkspaceLimits): LimitDraft {
  return {
    max_items: limits.max_items?.toString() ?? "",
    monthly_query_limit: limits.monthly_query_limit?.toString() ?? "",
    rate_limit_rpm: limits.rate_limit_rpm?.toString() ?? "",
  };
}

/** Blank means "no limit / default"; anything else must be a whole number of at least 1. */
function parseDraft(draft: LimitDraft): { limits: WorkspaceLimits } | { error: string } {
  const limits = {} as WorkspaceLimits;
  for (const { key, label } of LIMIT_FIELDS) {
    const raw = draft[key].trim().replace(/[,_\s]/g, "");
    if (!raw) {
      limits[key] = null;
    } else if (!/^\d+$/.test(raw) || Number(raw) < 1) {
      return { error: `${label} must be a whole number of at least 1, or blank.` };
    } else {
      limits[key] = Number(raw);
    }
  }
  return { limits };
}

function LimitsCard({ workspace }: { workspace: WorkspaceDetail }) {
  const update = useUpdateLimits();
  const [draft, setDraft] = React.useState<LimitDraft>(() => toDraft(workspace.limits));
  const [error, setError] = React.useState<string | null>(null);
  React.useEffect(() => setDraft(toDraft(workspace.limits)), [workspace.limits]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(toDraft(workspace.limits));

  const save = (event: React.FormEvent) => {
    event.preventDefault();
    const parsed = parseDraft(draft);
    if ("error" in parsed) {
      setError(parsed.error);
      return;
    }
    setError(null);
    update.mutate(
      { id: workspace.id, limits: parsed.limits },
      {
        onSuccess: () => toast.success("Limits saved"),
        onError: (e) => setError(apiErrorMessage(e)),
      },
    );
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Limits</CardTitle>
        <CardDescription>Apply immediately to every API key and session of this workspace.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={save} className="space-y-4" noValidate>
          {LIMIT_FIELDS.map(({ key, label, hint }) => (
            <div key={key} className="space-y-1.5">
              <Label htmlFor={key}>{label}</Label>
              <Input
                id={key}
                inputMode="numeric"
                placeholder={key === "rate_limit_rpm" ? "Default" : "No limit"}
                value={draft[key]}
                onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
              />
              <p className="text-xs text-muted-foreground">{hint}</p>
            </div>
          ))}
          <FieldError message={error ?? undefined} />
          <Button type="submit" disabled={!dirty || update.isPending}>
            {update.isPending && <Loader2 className="animate-spin" />}
            Save limits
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function SuspendDialog({
  workspace,
  open,
  onOpenChange,
}: {
  workspace: WorkspaceDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const suspend = useSuspendWorkspace();
  const [reason, setReason] = React.useState("");
  const close = (next: boolean) => {
    if (!next) setReason("");
    onOpenChange(next);
  };
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={close}
      title={`Suspend ${workspace.name}?`}
      description="Every API key and dashboard session of this workspace stops working at once, and its members cannot sign in. Nothing is deleted; you can reactivate it at any time."
      confirmLabel="Suspend workspace"
      destructive
      pending={suspend.isPending}
      confirmDisabled={!reason.trim()}
      onConfirm={() =>
        suspend.mutate(
          { id: workspace.id, reason: reason.trim() },
          {
            onSuccess: () => {
              toast.success(`${workspace.name} suspended`);
              close(false);
            },
            onError: (error) => toastApiError(error, "Could not suspend"),
          },
        )
      }
    >
      <div className="space-y-1.5">
        <Label htmlFor="suspendReason">Reason (internal)</Label>
        <Textarea
          id="suspendReason"
          value={reason}
          maxLength={500}
          onChange={(e) => setReason(e.target.value)}
          placeholder="e.g. Invoice 42 unpaid"
        />
        <p className="text-xs text-muted-foreground">
          For other admins. The workspace only sees that it is suspended.
        </p>
      </div>
    </ConfirmDialog>
  );
}

const STATUS_ORDER: EmbeddingStatus[] = ["DONE", "PENDING", "PROCESSING", "FAILED"];

export default function WorkspacePage({ params }: { params: { id: string } }) {
  const { data: me, isLoading: meLoading } = useMe();
  const isAdmin = Boolean(me?.user?.is_platform_admin);
  const { data: workspace, isLoading, isError } = useWorkspace(params.id);
  const activate = useActivateWorkspace();
  const [suspending, setSuspending] = React.useState(false);
  const isOwnWorkspace = me?.id === params.id;

  const back = (
    <Button variant="ghost" size="sm" asChild className="mb-4 -ml-2">
      <Link href="/dashboard/admin">
        <ArrowLeft /> All workspaces
      </Link>
    </Button>
  );

  if (meLoading) return null;
  if (!isAdmin) return <NotPlatformAdmin />;
  if (isError)
    return (
      <>
        {back}
        <EmptyState icon={SearchX} title="Workspace not found" description="It may have been deleted." />
      </>
    );
  if (isLoading || !workspace)
    return (
      <>
        {back}
        <Skeleton className="mb-6 h-10 w-72" />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
      </>
    );

  const limit = (value: number | null) => (value === null ? "" : ` / ${formatNumber(value)}`);
  const stats: Stat[] = [
    {
      label: "Items",
      value: `${formatNumber(workspace.items)}${limit(workspace.limits.max_items)}`,
      icon: Database,
      hint: `${formatNumber(workspace.embedding_status.FAILED)} failed · ${formatNumber(workspace.embedding_status.PENDING)} pending`,
    },
    {
      label: "Recommendations this month",
      value: `${formatNumber(workspace.queries_this_month)}${limit(workspace.limits.monthly_query_limit)}`,
      icon: Activity,
      hint: `Last active ${formatRelative(workspace.last_active_at).toLowerCase()}`,
    },
    {
      label: "OpenAI cost this month",
      value: formatCost(workspace.estimated_cost_this_month_usd),
      icon: Coins,
      hint: `${formatNumber(workspace.tokens_this_month)} tokens (${formatNumber(workspace.tokens_last_30_days)} in 30 days)`,
    },
    {
      label: "Members",
      value: formatNumber(workspace.members),
      icon: Users,
      hint: `Owner: ${workspace.owner_name ?? "—"}`,
    },
  ];

  const action =
    workspace.status === "active" ? (
      <Button
        variant="destructive"
        onClick={() => setSuspending(true)}
        disabled={isOwnWorkspace}
        title={isOwnWorkspace ? "You cannot suspend your own workspace" : undefined}
      >
        <Ban /> Suspend
      </Button>
    ) : (
      <Button
        disabled={activate.isPending}
        onClick={() =>
          activate.mutate(workspace.id, {
            onSuccess: () => toast.success(`${workspace.name} reactivated`),
            onError: (error) => toastApiError(error, "Could not reactivate"),
          })
        }
      >
        {activate.isPending ? <Loader2 className="animate-spin" /> : <PlayCircle />} Reactivate
      </Button>
    );

  return (
    <>
      {back}
      <PageHeader
        title={workspace.name}
        description={`${workspace.email} · ${workspace.domain_type} · created ${formatDate(workspace.created_at, false)}${workspace.email_verified ? "" : " · email not verified"}`}
        actions={
          <div className="flex items-center gap-3">
            <WorkspaceStatusBadge status={workspace.status} />
            {action}
          </div>
        }
      />

      {workspace.status === "suspended" && (
        <Card className="mb-6 border-destructive/40 bg-destructive/5">
          <CardContent className="p-4 text-sm">
            <p className="font-medium text-destructive">Suspended {formatDate(workspace.suspended_at)}</p>
            {workspace.suspended_reason && <p className="mt-1 text-muted-foreground">{workspace.suspended_reason}</p>}
          </CardContent>
        </Card>
      )}

      <div className="space-y-6">
        <OverviewCards stats={stats} />

        <div className="grid gap-6 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle className="text-base">Recommendations</CardTitle>
              <CardDescription>Last 30 days (UTC)</CardDescription>
            </CardHeader>
            <CardContent>
              <RecommendationChart
                variant="area"
                daily={workspace.queries_daily.map((d) => ({ ...d, avg_latency_ms: null }))}
              />
            </CardContent>
          </Card>
          <LimitsCard workspace={workspace} />
        </div>

        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Members</CardTitle>
            </CardHeader>
            <CardContent className="px-0">
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="pl-5">Member</TableHead>
                    <TableHead>Role</TableHead>
                    <TableHead className="pr-5">Last sign-in</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {workspace.member_list.map((m) => (
                    <TableRow key={m.id}>
                      <TableCell className="pl-5">
                        <p className="font-medium">{m.name}</p>
                        <p className="text-xs text-muted-foreground">{m.email}</p>
                      </TableCell>
                      <TableCell>
                        <Badge variant="secondary">{ROLE_INFO[m.role].label}</Badge>
                      </TableCell>
                      <TableCell className="pr-5 text-muted-foreground">{formatRelative(m.last_login_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Integration API keys</CardTitle>
              <CardDescription>Dashboard sessions are not listed.</CardDescription>
            </CardHeader>
            <CardContent className="px-0">
              {workspace.api_keys.length ? (
                <Table>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead className="pl-5">Key</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead className="pr-5">Last used</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {workspace.api_keys.map((k) => (
                      <TableRow key={k.id}>
                        <TableCell className="pl-5">
                          <p className="font-medium">{k.name}</p>
                          <p className="font-mono text-xs text-muted-foreground">{k.display_key}</p>
                        </TableCell>
                        <TableCell>
                          <Badge variant={k.is_active ? "success" : "muted"}>{k.is_active ? "Active" : "Revoked"}</Badge>
                        </TableCell>
                        <TableCell className="pr-5 text-muted-foreground">{formatRelative(k.last_used_at)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <p className="px-5 text-sm text-muted-foreground">No integration keys yet.</p>
              )}
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Items by embedding status</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              {STATUS_ORDER.map((status) => (
                <div key={status} className="rounded-lg border p-3">
                  <dt className="text-xs text-muted-foreground">{status}</dt>
                  <dd className="mt-1 text-xl font-semibold tabular-nums">
                    {formatNumber(workspace.embedding_status[status])}
                  </dd>
                </div>
              ))}
            </dl>
          </CardContent>
        </Card>
      </div>

      <SuspendDialog workspace={workspace} open={suspending} onOpenChange={setSuspending} />
    </>
  );
}
