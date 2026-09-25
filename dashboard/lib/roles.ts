import type { Role, Tenant } from "@/types";

/** Least to most privileged; mirrors the API's Role enum. */
export const ROLES: Role[] = ["VIEWER", "DEVELOPER", "ADMIN", "OWNER"];

export const ROLE_INFO: Record<Role, { label: string; description: string }> = {
  VIEWER: { label: "Viewer", description: "Browse items and analytics, try recommendations." },
  DEVELOPER: { label: "Developer", description: "Also upload and delete items, rebuild the index, manage API keys." },
  ADMIN: { label: "Admin", description: "Also change the domain config and manage the team." },
  OWNER: { label: "Owner", description: "Everything, including transferring ownership and deleting the workspace." },
};

/** Roles an Admin can hand out; ownership moves only by transfer. */
export const ASSIGNABLE_ROLES: Role[] = ["ADMIN", "DEVELOPER", "VIEWER"];

export function atLeast(role: Role | undefined, minimum: Role): boolean {
  return role !== undefined && ROLES.indexOf(role) >= ROLES.indexOf(minimum);
}

/** Whether the signed-in caller may do something that needs `minimum`. */
export function can(me: Tenant | undefined, minimum: Role): boolean {
  return atLeast(me?.role, minimum);
}

export function needsRole(minimum: Role): string {
  return `Needs the ${ROLE_INFO[minimum].label} role or higher`;
}
