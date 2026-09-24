"use client";

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, X, XCircle } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/controls";
import { itemKeys, useBatchStatus } from "@/lib/hooks/items";

/** Live progress for an async upload. Refreshes the item list once the batch completes. */
export function BatchProgress({ batchId, onDismiss }: { batchId: string; onDismiss: () => void }) {
  const { data: batch, isError } = useBatchStatus(batchId);
  const queryClient = useQueryClient();
  const announced = React.useRef(false);
  const complete = batch?.status === "DONE" || batch?.status === "PARTIAL_FAIL";

  React.useEffect(() => {
    if (!complete || announced.current || !batch) return;
    announced.current = true;
    queryClient.invalidateQueries({ queryKey: itemKeys.all });
    queryClient.invalidateQueries({ queryKey: ["analytics"] });
    if (batch.status === "DONE") {
      toast.success(`Upload complete: ${batch.processed_items} items embedded`);
    } else {
      toast.warning(`Upload finished with ${batch.failed_items} failed items`, {
        description: "Filter the table by FAILED to see why.",
      });
    }
  }, [complete, batch, queryClient]);

  const handled = batch ? batch.processed_items + batch.failed_items : 0;
  return (
    <Card className="mb-4" role="status" aria-live="polite">
      <CardContent className="flex items-center gap-4 p-4">
        {complete ? (
          batch.status === "DONE" ? (
            <CheckCircle2 className="h-5 w-5 shrink-0 text-success" />
          ) : (
            <XCircle className="h-5 w-5 shrink-0 text-warning" />
          )
        ) : (
          <Loader2 className="h-5 w-5 shrink-0 animate-spin text-primary" />
        )}
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
            <span className="font-medium">
              {isError
                ? "Could not load batch status"
                : complete
                  ? "Batch complete"
                  : "Embedding your items…"}
            </span>
            {batch && (
              <span className="text-muted-foreground">
                {handled}/{batch.total_items} processed
                {batch.failed_items > 0 && (
                  <span className="text-destructive"> · {batch.failed_items} failed</span>
                )}
              </span>
            )}
          </div>
          <Progress
            value={batch?.progress_percentage ?? 0}
            indicatorClassName={batch?.status === "PARTIAL_FAIL" ? "bg-warning" : undefined}
          />
        </div>
        <Button variant="ghost" size="icon" onClick={onDismiss} aria-label="Dismiss">
          <X />
        </Button>
      </CardContent>
    </Card>
  );
}
