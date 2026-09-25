"use client";

import * as React from "react";
import { AlertTriangle, KeyRound, Loader2, MailWarning, Plus } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { FieldError, Label } from "@/components/ui/label";
import { CopyButton, EmptyState, PageHeader } from "@/components/ui/misc";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toastApiError } from "@/lib/api";
import { useApiKeys, useCreateApiKey, useMe, useRevokeApiKey } from "@/lib/hooks/account";
import { formatDate, formatRelative } from "@/lib/utils";
import type { ApiKey, ApiKeyCreated } from "@/types";

function keyStatus(key: ApiKey): { label: string; variant: "success" | "muted" | "warning" } {
  if (!key.is_active) return { label: "Revoked", variant: "muted" };
  if (key.expires_at && new Date(key.expires_at) <= new Date()) return { label: "Expired", variant: "warning" };
  return { label: "Active", variant: "success" };
}

function CreateKeyDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [name, setName] = React.useState("");
  const [created, setCreated] = React.useState<ApiKeyCreated | null>(null);
  const create = useCreateApiKey();
  const nameError = name.trim().length > 100 ? "At most 100 characters" : undefined;

  const close = (next: boolean) => {
    if (!next) {
      setName("");
      setCreated(null);
      create.reset();
    }
    onOpenChange(next);
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim() || nameError) return;
    create.mutate(name.trim(), {
      onSuccess: (key) => {
        setCreated(key);
        toast.success(`Created “${key.name}”`);
      },
      onError: (error) => toastApiError(error, "Could not create the key"),
    });
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent>
        {created ? (
          <>
            <DialogHeader>
              <DialogTitle>Your new API key</DialogTitle>
              <DialogDescription>Copy it now and store it somewhere safe.</DialogDescription>
            </DialogHeader>
            <div className="flex items-start gap-3 rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
              <p>
                <strong>Save this key — it will not be shown again.</strong>
              </p>
            </div>
            <div className="flex gap-2">
              <Input readOnly value={created.api_key} className="font-mono text-xs" onFocus={(e) => e.target.select()} aria-label="API key" />
              <CopyButton value={created.api_key} label="API key copied" />
            </div>
            <DialogFooter>
              <Button onClick={() => close(false)}>Done</Button>
            </DialogFooter>
          </>
        ) : (
          <form onSubmit={submit} className="grid gap-4">
            <DialogHeader>
              <DialogTitle>Create API key</DialogTitle>
              <DialogDescription>Give it a name that says where it is used.</DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              <Label htmlFor="keyName">Key name</Label>
              <Input
                id="keyName"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="production-backend"
                autoFocus
                aria-invalid={Boolean(nameError)}
              />
              <FieldError message={nameError} />
            </div>
            <DialogFooter>
              <Button type="submit" disabled={!name.trim() || Boolean(nameError) || create.isPending}>
                {create.isPending && <Loader2 className="animate-spin" />}
                Create key
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default function ApiKeysPage() {
  const { data: keys, isLoading } = useApiKeys();
  const revoke = useRevokeApiKey();
  const [creating, setCreating] = React.useState(false);
  const [revoking, setRevoking] = React.useState<ApiKey | null>(null);
  const { data: me } = useMe();
  const unverified = me !== undefined && !me.email_verified;

  const create = (
    <Button
      onClick={() => setCreating(true)}
      disabled={unverified}
      title={unverified ? "Verify your email to create API keys" : undefined}
    >
      <Plus /> Create new key
    </Button>
  );

  return (
    <>
      <PageHeader
        title="API keys"
        description="Send a key as the X-API-Key header. Dashboard sign-in sessions are not listed."
        actions={create}
      />
      <Card>
        {isLoading ? (
          <div className="space-y-3 p-5">
            {Array.from({ length: 3 }, (_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : keys?.length ? (
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>Name</TableHead>
                <TableHead>Key</TableHead>
                <TableHead className="hidden md:table-cell">Created</TableHead>
                <TableHead className="hidden sm:table-cell">Last used</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {keys.map((key) => {
                const status = keyStatus(key);
                return (
                  <TableRow key={key.id}>
                    <TableCell className="font-medium">{key.name}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{key.display_key}</TableCell>
                    <TableCell className="hidden text-muted-foreground md:table-cell">{formatDate(key.created_at, false)}</TableCell>
                    <TableCell className="hidden text-muted-foreground sm:table-cell" title={key.last_used_at ?? undefined}>
                      {formatRelative(key.last_used_at)}
                    </TableCell>
                    <TableCell>
                      <Badge variant={status.variant}>{status.label}</Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      {key.is_active && (
                        <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" onClick={() => setRevoking(key)}>
                          Revoke
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        ) : (
          <div className="p-4">
            {unverified ? (
              <EmptyState
                icon={MailWarning}
                title="Verify your email first"
                description={`Open the link we sent to ${me.email} to create API keys. Use the banner above to get a new link.`}
              />
            ) : (
              <EmptyState icon={KeyRound} title="No API keys" description="Create a key to call the API from your application." action={create} />
            )}
          </div>
        )}
      </Card>

      <CreateKeyDialog open={creating} onOpenChange={setCreating} />
      <ConfirmDialog
        open={revoking !== null}
        onOpenChange={(open) => !open && setRevoking(null)}
        title={`Revoke “${revoking?.name}”?`}
        description="Applications using this key will get 401 errors immediately. This cannot be undone."
        confirmLabel="Revoke key"
        destructive
        pending={revoke.isPending}
        onConfirm={() =>
          revoking &&
          revoke.mutate(revoking.id, {
            onSuccess: () => {
              toast.success(`Revoked “${revoking.name}”`);
              setRevoking(null);
            },
            onError: (error) => toastApiError(error, "Could not revoke the key"),
          })
        }
      />
    </>
  );
}
