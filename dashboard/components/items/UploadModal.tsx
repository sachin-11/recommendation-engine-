"use client";

import * as React from "react";
import { AlertCircle, AlertTriangle, Loader2, Upload } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/input";
import { JsonView } from "@/components/ui/json-view";
import { toastApiError } from "@/lib/api";
import { exampleItemFor } from "@/lib/domains";
import { useUploadItems } from "@/lib/hooks/items";
import { checkItemsJson } from "@/lib/validators";
import type { Tenant } from "@/types";

/** Paste a JSON array, check it against the domain config, preview, then upload (async). */
export function UploadModal({
  open,
  onOpenChange,
  tenant,
  onQueued,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  tenant: Tenant;
  onQueued: (batchId: string) => void;
}) {
  const [text, setText] = React.useState("");
  const [checked, setChecked] = React.useState(false);
  const upload = useUploadItems();
  const check = React.useMemo(
    () => (text.trim() ? checkItemsJson(text, tenant.domain_config) : null),
    [text, tenant.domain_config],
  );
  const example = exampleItemFor(tenant.domain_config, tenant.domain_type);

  const reset = () => {
    setText("");
    setChecked(false);
  };

  const confirm = () => {
    if (!check || check.errors.length) return;
    upload.mutate(check.items, {
      onSuccess: (batch) => {
        toast.success(`Queued ${batch.total_items} items for embedding`);
        onQueued(batch.batch_id);
        reset();
        onOpenChange(false);
      },
      onError: (error) => toastApiError(error, "Upload failed"),
    });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Upload JSON</DialogTitle>
          <DialogDescription>
            Paste an array of up to 1000 {tenant.domain_config.item_label}s. Each needs an{" "}
            <code className="font-mono">external_id</code>; re-uploading an id updates it.
          </DialogDescription>
        </DialogHeader>

        {!checked ? (
          <div className="space-y-3">
            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={12}
              spellCheck={false}
              className="font-mono text-xs"
              placeholder={JSON.stringify([example], null, 2)}
              aria-label="Items JSON"
              aria-invalid={Boolean(check?.errors.length)}
            />
            {check?.errors.map((error) => (
              <p key={error} className="flex items-start gap-1.5 text-xs text-destructive">
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {error}
              </p>
            ))}
            {check && !check.errors.length && (
              <p className="text-xs text-success">{check.items.length} items look valid.</p>
            )}
            <Button
              variant="link"
              className="h-auto p-0 text-xs"
              onClick={() => setText(JSON.stringify([example], null, 2))}
            >
              Insert an example
            </Button>
          </div>
        ) : (
          check && (
            <div className="space-y-3">
              <p className="text-sm">
                Uploading <strong>{check.items.length}</strong> items. First{" "}
                {Math.min(3, check.items.length)}:
              </p>
              <JsonView value={check.items.slice(0, 3)} className="max-h-72" />
              {check.warnings.map((warning) => (
                <p key={warning} className="flex items-start gap-1.5 text-xs text-warning">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {warning}
                </p>
              ))}
            </div>
          )
        )}

        <DialogFooter>
          {checked ? (
            <>
              <Button variant="outline" onClick={() => setChecked(false)} disabled={upload.isPending}>
                Back
              </Button>
              <Button onClick={confirm} disabled={upload.isPending}>
                {upload.isPending ? <Loader2 className="animate-spin" /> : <Upload />}
                Confirm upload
              </Button>
            </>
          ) : (
            <Button onClick={() => setChecked(true)} disabled={!check || check.errors.length > 0}>
              Preview
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
