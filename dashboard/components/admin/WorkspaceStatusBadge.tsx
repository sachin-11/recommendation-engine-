import { ShieldAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/misc";
import type { WorkspaceBilling, WorkspaceStatus } from "@/types";

export function WorkspaceStatusBadge({ status }: { status: WorkspaceStatus }) {
  return status === "active" ? (
    <Badge variant="success">Active</Badge>
  ) : (
    <Badge variant="destructive">Suspended</Badge>
  );
}

/** The plan in force, and how: paid, complimentary, or paid with the last payment failed. */
export function WorkspacePlanBadge({ billing }: { billing: WorkspaceBilling }) {
  if (billing.complimentary)
    return (
      <Badge variant="success" title="Complimentary Pro, given by a platform admin">
        Pro · complimentary
      </Badge>
    );
  if (billing.plan === "PRO" && billing.subscription_status === "past_due")
    return (
      <Badge variant="warning" title="The last payment failed; Stripe is retrying">
        Pro · past due
      </Badge>
    );
  return billing.plan === "PRO" ? <Badge>Pro</Badge> : <Badge variant="muted">Free</Badge>;
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
