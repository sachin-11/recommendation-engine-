"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Loader2, RefreshCw, Save } from "lucide-react";
import { toast } from "sonner";

import { configErrors, DomainConfigEditor } from "@/components/onboarding/DomainConfigEditor";
import { BatchProgress } from "@/components/items/BatchProgress";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/misc";
import { Skeleton } from "@/components/ui/skeleton";
import { toastApiError } from "@/lib/api";
import { presetFor } from "@/lib/domains";
import { useDeleteAccount, useMe, useUpdateDomainConfig } from "@/lib/hooks/account";
import { useDeleteAllItems, useIndexStats, useRebuildIndex } from "@/lib/hooks/items";
import { formatNumber } from "@/lib/utils";
import type { DomainConfig, Tenant } from "@/types";

const same = (a: DomainConfig, b: DomainConfig) => JSON.stringify(a) === JSON.stringify(b);

function DomainConfigSection({ tenant }: { tenant: Tenant }) {
  const [config, setConfig] = React.useState<DomainConfig>(tenant.domain_config);
  const [confirmRebuild, setConfirmRebuild] = React.useState(false);
  const [rebuildBatch, setRebuildBatch] = React.useState<string | null>(null);
  const save = useUpdateDomainConfig();
  const rebuild = useRebuildIndex();
  const stats = useIndexStats();
  const dirty = !same(config, tenant.domain_config);
  const invalid = Object.keys(configErrors(config)).length > 0;
  const primaryChanged = config.primary_embedding_field !== tenant.domain_config.primary_embedding_field;

  const onSave = () =>
    save.mutate(config, {
      onSuccess: ({ rebuild_recommended }) => {
        if (rebuild_recommended) {
          toast.success("Config saved", {
            description: "Embedded fields or filters changed. Rebuild the index to apply them to existing items.",
            action: { label: "Rebuild", onClick: () => setConfirmRebuild(true) },
          });
        } else {
          toast.success("Config saved");
        }
      },
      onError: (error) => toastApiError(error, "Could not save the config"),
    });

  const onRebuild = () =>
    rebuild.mutate(undefined, {
      onSuccess: (batch) => {
        setConfirmRebuild(false);
        if (batch.total_items) setRebuildBatch(batch.batch_id);
        toast.success(batch.total_items ? `Re-embedding ${batch.total_items} items` : "No items to rebuild");
      },
      onError: (error) => toastApiError(error, "Could not start the rebuild"),
    });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Domain config</CardTitle>
        <CardDescription>
          Which fields are embedded and which can be filtered on. Domain:{" "}
          <Badge variant="secondary">{presetFor(tenant.domain_type).label}</Badge>
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {rebuildBatch && <BatchProgress batchId={rebuildBatch} onDismiss={() => setRebuildBatch(null)} />}
        <div className="flex items-start gap-3 rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
          <p>
            Changing <code className="font-mono">primary_embedding_field</code> requires rebuilding your
            index. So do changes to searchable or filter fields, for items already uploaded.
            {primaryChanged && <strong> You have changed it.</strong>}
          </p>
        </div>
        <DomainConfigEditor value={config} onChange={setConfig} />
        <p className="text-xs text-muted-foreground">
          Index{" "}
          {stats.isLoading ? (
            <Skeleton className="inline-block h-3 w-24 align-middle" />
          ) : stats.data ? (
            <>
              <code className="font-mono">{stats.data.index_name}</code>:{" "}
              {formatNumber(stats.data.total_vector_count)} vectors
            </>
          ) : (
            "stats unavailable"
          )}
        </p>
      </CardContent>
      <CardFooter className="flex flex-wrap justify-end gap-2">
        {dirty && (
          <Button variant="ghost" onClick={() => setConfig(tenant.domain_config)} disabled={save.isPending}>
            Discard changes
          </Button>
        )}
        <Button variant="outline" onClick={() => setConfirmRebuild(true)}>
          <RefreshCw /> Rebuild index
        </Button>
        <Button onClick={onSave} disabled={!dirty || invalid || save.isPending}>
          {save.isPending ? <Loader2 className="animate-spin" /> : <Save />}
          Save config
        </Button>
      </CardFooter>
      <ConfirmDialog
        open={confirmRebuild}
        onOpenChange={setConfirmRebuild}
        title="Rebuild the index?"
        description={
          <>
            Every item is re-embedded with the <strong>saved</strong> config
            {dirty && " (unsaved changes are not used)"}. This calls OpenAI once per item and
            runs in the background.
          </>
        }
        confirmLabel="Rebuild"
        pending={rebuild.isPending}
        onConfirm={onRebuild}
      />
    </Card>
  );
}

function DangerZone({ tenant }: { tenant: Tenant }) {
  const router = useRouter();
  const [dialog, setDialog] = React.useState<"items" | "account" | null>(null);
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const deleteItems = useDeleteAllItems();
  const deleteAccount = useDeleteAccount();

  const closeDialog = (open: boolean) => {
    if (!open) {
      setDialog(null);
      setEmail("");
      setPassword("");
    }
  };

  return (
    <Card className="border-destructive/40">
      <CardHeader>
        <CardTitle className="text-base text-destructive">Danger zone</CardTitle>
        <CardDescription>These actions cannot be undone.</CardDescription>
      </CardHeader>
      <CardContent className="divide-y">
        <div className="flex flex-col gap-3 pb-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="font-medium">Delete all items</p>
            <p className="text-sm text-muted-foreground">Removes every item and its vector. Keys and settings stay.</p>
          </div>
          <Button variant="outline" className="border-destructive/50 text-destructive hover:bg-destructive/10 hover:text-destructive" onClick={() => setDialog("items")}>
            Delete all items
          </Button>
        </div>
        <div className="flex flex-col gap-3 pt-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="font-medium">Delete account</p>
            <p className="text-sm text-muted-foreground">Deletes the tenant, items, API keys, logs and the vector index.</p>
          </div>
          <Button variant="destructive" onClick={() => setDialog("account")}>
            Delete account
          </Button>
        </div>
      </CardContent>

      <ConfirmDialog
        open={dialog === "items"}
        onOpenChange={closeDialog}
        title="Delete all items?"
        description="Every item is removed from the database and from your vector index."
        confirmLabel="Delete all items"
        destructive
        pending={deleteItems.isPending}
        onConfirm={() =>
          deleteItems.mutate(undefined, {
            onSuccess: ({ deleted }) => {
              toast.success(`Deleted ${deleted} items`);
              closeDialog(false);
            },
            onError: (error) => toastApiError(error, "Could not delete items"),
          })
        }
      />

      <ConfirmDialog
        open={dialog === "account"}
        onOpenChange={closeDialog}
        title="Delete your account?"
        description={
          <>
            This permanently deletes <strong>{tenant.name}</strong> and everything in it. Type your
            account email{tenant.has_password ? " and password" : ""} to confirm.
          </>
        }
        confirmLabel="Delete account"
        destructive
        pending={deleteAccount.isPending}
        confirmDisabled={email.trim().toLowerCase() !== tenant.email || (tenant.has_password && !password)}
        onConfirm={() =>
          deleteAccount.mutate(
            { confirm_email: email.trim(), password: tenant.has_password ? password : undefined },
            {
              onSuccess: () => {
                toast.success("Account deleted");
                router.replace("/register");
              },
              onError: (error) => toastApiError(error, "Could not delete the account"),
            },
          )
        }
      >
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="confirmEmail">
              Email <span className="font-mono text-xs text-muted-foreground">({tenant.email})</span>
            </Label>
            <Input id="confirmEmail" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="off" />
          </div>
          {tenant.has_password && (
            <div className="space-y-1.5">
              <Label htmlFor="confirmPassword">Password</Label>
              <Input id="confirmPassword" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
            </div>
          )}
        </div>
      </ConfirmDialog>
    </Card>
  );
}

export default function SettingsPage() {
  const { data: me, isLoading } = useMe();
  return (
    <>
      <PageHeader title="Settings" description="Your domain configuration and account." />
      {isLoading || !me ? (
        <div className="space-y-6">
          <Skeleton className="h-96" />
          <Skeleton className="h-40" />
        </div>
      ) : (
        <div className="space-y-6">
          <DomainConfigSection key={me.updated_at} tenant={me} />
          <DangerZone tenant={me} />
        </div>
      )}
    </>
  );
}
