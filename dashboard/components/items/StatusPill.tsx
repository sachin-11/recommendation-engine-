import { CheckCircle2, CircleDashed, Loader2, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { EmbeddingStatus } from "@/types";

const STYLES: Record<
  EmbeddingStatus,
  { variant: "muted" | "info" | "success" | "destructive"; icon: typeof CheckCircle2; label: string }
> = {
  PENDING: { variant: "muted", icon: CircleDashed, label: "Pending" },
  PROCESSING: { variant: "info", icon: Loader2, label: "Processing" },
  DONE: { variant: "success", icon: CheckCircle2, label: "Done" },
  FAILED: { variant: "destructive", icon: XCircle, label: "Failed" },
};

export function StatusPill({ status }: { status: EmbeddingStatus }) {
  const { variant, icon: Icon, label } = STYLES[status];
  return (
    <Badge variant={variant}>
      <Icon className={status === "PROCESSING" ? "animate-spin" : undefined} />
      {label}
    </Badge>
  );
}
