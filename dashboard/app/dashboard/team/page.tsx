"use client";

import * as React from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { Crown, Loader2, Mail, UserPlus, Users } from "lucide-react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input, NativeSelect } from "@/components/ui/input";
import { FieldError, Label } from "@/components/ui/label";
import { EmptyState, PageHeader } from "@/components/ui/misc";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { apiErrorMessage, toastApiError } from "@/lib/api";
import { useMe } from "@/lib/hooks/account";
import {
  useChangeRole,
  useInvite,
  useRemoveMember,
  useRevokeInvitation,
  useTeam,
  useTransferOwnership,
} from "@/lib/hooks/team";
import { ASSIGNABLE_ROLES, can, ROLE_INFO } from "@/lib/roles";
import { formatDate, formatRelative } from "@/lib/utils";
import { inviteSchema, type InviteValues } from "@/lib/validators";
import type { Role, User } from "@/types";

const ROLE_BADGE: Record<Role, "default" | "info" | "secondary" | "muted"> = {
  OWNER: "default",
  ADMIN: "info",
  DEVELOPER: "secondary",
  VIEWER: "muted",
};

function RoleBadge({ role }: { role: Role }) {
  return (
    <Badge variant={ROLE_BADGE[role]}>
      {role === "OWNER" && <Crown className="h-3 w-3" />}
      {ROLE_INFO[role].label}
    </Badge>
  );
}

function initials(name: string) {
  return name
    .split(/\s+/)
    .map((word) => word[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

function InviteDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const invite = useInvite();
  const form = useForm<InviteValues>({
    resolver: zodResolver(inviteSchema),
    defaultValues: { email: "", role: "DEVELOPER" },
  });
  const role = form.watch("role");

  const close = (next: boolean) => {
    if (!next) {
      form.reset();
      invite.reset();
    }
    onOpenChange(next);
  };

  const onSubmit = form.handleSubmit((values) =>
    invite.mutate(values, {
      onSuccess: (invitation) => {
        toast.success(`Invitation sent to ${invitation.email}`);
        close(false);
      },
    }),
  );

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent>
        <form onSubmit={onSubmit} className="grid gap-4" noValidate>
          <DialogHeader>
            <DialogTitle>Invite a team member</DialogTitle>
            <DialogDescription>They get an email with a link to choose their name and password.</DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="inviteEmail">Email</Label>
            <Input
              id="inviteEmail"
              type="email"
              placeholder="teammate@company.com"
              autoFocus
              aria-invalid={Boolean(form.formState.errors.email)}
              {...form.register("email")}
            />
            <FieldError message={form.formState.errors.email?.message} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="inviteRole">Role</Label>
            <NativeSelect id="inviteRole" {...form.register("role")}>
              {ASSIGNABLE_ROLES.map((r) => (
                <option key={r} value={r}>
                  {ROLE_INFO[r].label}
                </option>
              ))}
            </NativeSelect>
            <p className="text-xs text-muted-foreground">{ROLE_INFO[role].description}</p>
          </div>
          {invite.isError && <FieldError message={apiErrorMessage(invite.error)} />}
          <DialogFooter>
            <Button type="submit" disabled={invite.isPending}>
              {invite.isPending ? <Loader2 className="animate-spin" /> : <Mail />}
              Send invitation
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function MemberRow({
  member,
  me,
  canManage,
  isOwner,
  onRemove,
  onTransfer,
}: {
  member: User;
  me: User | null;
  canManage: boolean;
  isOwner: boolean;
  onRemove: (member: User) => void;
  onTransfer: (member: User) => void;
}) {
  const changeRole = useChangeRole();
  const isSelf = me?.id === member.id;
  const editable = canManage && !isSelf && member.role !== "OWNER";

  return (
    <TableRow>
      <TableCell>
        <div className="flex items-center gap-3">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/15 text-xs font-semibold text-primary">
            {initials(member.name)}
          </span>
          <div className="min-w-0">
            <p className="truncate font-medium">
              {member.name} {isSelf && <span className="text-xs font-normal text-muted-foreground">(you)</span>}
            </p>
            <p className="truncate text-xs text-muted-foreground">{member.email}</p>
          </div>
        </div>
      </TableCell>
      <TableCell>
        {editable ? (
          <NativeSelect
            aria-label={`Role of ${member.name}`}
            className="h-8 w-36"
            value={member.role}
            disabled={changeRole.isPending}
            onChange={(e) =>
              changeRole.mutate(
                { id: member.id, role: e.target.value as Role },
                {
                  onSuccess: (user) => toast.success(`${user.name} is now ${ROLE_INFO[user.role].label}`),
                  onError: (error) => toastApiError(error, "Could not change the role"),
                },
              )
            }
          >
            {ASSIGNABLE_ROLES.map((r) => (
              <option key={r} value={r}>
                {ROLE_INFO[r].label}
              </option>
            ))}
          </NativeSelect>
        ) : (
          <RoleBadge role={member.role} />
        )}
      </TableCell>
      <TableCell className="hidden text-muted-foreground md:table-cell" title={member.last_login_at ?? undefined}>
        {formatRelative(member.last_login_at)}
      </TableCell>
      <TableCell className="hidden text-muted-foreground lg:table-cell">{formatDate(member.created_at, false)}</TableCell>
      <TableCell className="text-right">
        <div className="flex justify-end gap-1">
          {isOwner && !isSelf && member.email_verified && (
            <Button variant="ghost" size="sm" onClick={() => onTransfer(member)}>
              Make owner
            </Button>
          )}
          {editable && (
            <Button
              variant="ghost"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={() => onRemove(member)}
            >
              Remove
            </Button>
          )}
        </div>
      </TableCell>
    </TableRow>
  );
}

export default function TeamPage() {
  const { data: me } = useMe();
  const { data: team, isLoading } = useTeam();
  const revoke = useRevokeInvitation();
  const remove = useRemoveMember();
  const transfer = useTransferOwnership();
  const [inviting, setInviting] = React.useState(false);
  const [removing, setRemoving] = React.useState<User | null>(null);
  const [transferring, setTransferring] = React.useState<User | null>(null);
  const [password, setPassword] = React.useState("");

  const canManage = can(me, "ADMIN");
  const isOwner = me?.role === "OWNER";
  const invite = (
    <Button onClick={() => setInviting(true)} disabled={!canManage} title={canManage ? undefined : "Only Admins can invite"}>
      <UserPlus /> Invite member
    </Button>
  );

  return (
    <>
      <PageHeader
        title="Team"
        description="Everyone who can sign in to this workspace, and what they are allowed to do."
        actions={invite}
      />

      <div className="space-y-6">
        <Card>
          {isLoading || !team ? (
            <div className="space-y-3 p-5">
              {Array.from({ length: 3 }, (_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>Member</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead className="hidden md:table-cell">Last sign-in</TableHead>
                  <TableHead className="hidden lg:table-cell">Joined</TableHead>
                  <TableHead className="text-right">
                    <span className="sr-only">Actions</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {team.members.map((member) => (
                  <MemberRow
                    key={member.id}
                    member={member}
                    me={me?.user ?? null}
                    canManage={canManage}
                    isOwner={isOwner}
                    onRemove={setRemoving}
                    onTransfer={setTransferring}
                  />
                ))}
              </TableBody>
            </Table>
          )}
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Pending invitations</CardTitle>
            <CardDescription>Links expire after 7 days. Inviting the same email again sends a fresh link.</CardDescription>
          </CardHeader>
          <CardContent className="px-0">
            {team?.invitations.length ? (
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="pl-5">Email</TableHead>
                    <TableHead>Role</TableHead>
                    <TableHead className="hidden sm:table-cell">Expires</TableHead>
                    <TableHead className="pr-5 text-right">
                      <span className="sr-only">Actions</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {team.invitations.map((invitation) => (
                    <TableRow key={invitation.id}>
                      <TableCell className="pl-5 font-medium">{invitation.email}</TableCell>
                      <TableCell>
                        <RoleBadge role={invitation.role} />
                      </TableCell>
                      <TableCell className="hidden text-muted-foreground sm:table-cell">{formatDate(invitation.expires_at)}</TableCell>
                      <TableCell className="pr-5 text-right">
                        {canManage && (
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={revoke.isPending}
                            onClick={() =>
                              revoke.mutate(invitation.id, {
                                onSuccess: () => toast.success(`Invitation to ${invitation.email} revoked`),
                                onError: (error) => toastApiError(error, "Could not revoke"),
                              })
                            }
                          >
                            Revoke
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <div className="px-5">
                <EmptyState
                  icon={Users}
                  title="No pending invitations"
                  description={canManage ? "Invite teammates to share this workspace." : "Ask an Admin to invite teammates."}
                />
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Roles</CardTitle>
            <CardDescription>
              Integration API keys act as Developers. You are{" "}
              <strong>{me ? ROLE_INFO[me.role].label : "…"}</strong>.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="grid gap-3 sm:grid-cols-2">
              {(["OWNER", "ADMIN", "DEVELOPER", "VIEWER"] as Role[]).map((role) => (
                <div key={role} className="rounded-lg border p-3">
                  <dt>
                    <RoleBadge role={role} />
                  </dt>
                  <dd className="mt-2 text-sm text-muted-foreground">{ROLE_INFO[role].description}</dd>
                </div>
              ))}
            </dl>
          </CardContent>
        </Card>
      </div>

      <InviteDialog open={inviting} onOpenChange={setInviting} />

      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => !open && setRemoving(null)}
        title={`Remove ${removing?.name}?`}
        description="They are signed out and can no longer open this workspace. API keys they created keep working; revoke them separately if needed."
        confirmLabel="Remove member"
        destructive
        pending={remove.isPending}
        onConfirm={() =>
          removing &&
          remove.mutate(removing.id, {
            onSuccess: () => {
              toast.success(`${removing.name} removed`);
              setRemoving(null);
            },
            onError: (error) => toastApiError(error, "Could not remove the member"),
          })
        }
      />

      <ConfirmDialog
        open={transferring !== null}
        onOpenChange={(open) => {
          if (!open) {
            setTransferring(null);
            setPassword("");
          }
        }}
        title={`Make ${transferring?.name} the owner?`}
        description={
          <>
            <strong>{transferring?.email}</strong> becomes the owner and the account email. You
            become an Admin and can no longer delete the workspace.
          </>
        }
        confirmLabel="Transfer ownership"
        destructive
        pending={transfer.isPending}
        confirmDisabled={Boolean(me?.has_password) && !password}
        onConfirm={() =>
          transferring &&
          transfer.mutate(
            { user_id: transferring.id, password: me?.has_password ? password : undefined },
            {
              onSuccess: (user) => {
                toast.success(`${user.name} is now the owner`);
                setTransferring(null);
                setPassword("");
              },
              onError: (error) => toastApiError(error, "Could not transfer ownership"),
            },
          )
        }
      >
        {me?.has_password && (
          <div className="space-y-1.5">
            <Label htmlFor="transferPassword">Your password</Label>
            <Input
              id="transferPassword"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </div>
        )}
      </ConfirmDialog>
    </>
  );
}
