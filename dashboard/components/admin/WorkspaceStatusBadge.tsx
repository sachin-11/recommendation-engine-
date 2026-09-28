import { ShieldAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/misc";
import type { WorkspaceStatus } from "@/types";

export function WorkspaceStatusBadge({ status }: { status: WorkspaceStatus }) {
  return status === "active" ? (
    <Badge variant="success">Active</Badge>
  ) : (
    <Badge variant="destructive">Suspended</Badge>
  );
}

/** Shown to anyone who opens the admin area without platform admin rights. */
export function NotPlatformAdmin() {
  return (
    <EmptyState
      icon={ShieldAlert}
      title="Platform admins only"
      description="This area manages every workspace on the platform. Ask the operator to grant you access."
    />
  );
}
