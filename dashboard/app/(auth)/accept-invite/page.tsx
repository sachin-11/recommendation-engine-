"use client";

import * as React from "react";
import { Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2, MailX, Users } from "lucide-react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { FieldError, Label } from "@/components/ui/label";
import { apiErrorMessage } from "@/lib/api";
import { useAcceptInvitation, useInvitationInfo } from "@/lib/hooks/team";
import { ROLE_INFO } from "@/lib/roles";
import { acceptInviteSchema, type AcceptInviteValues } from "@/lib/validators";

function AcceptInvite() {
  const router = useRouter();
  const params = useSearchParams();
  // Keep the token in memory only and drop it from the address bar and history.
  const [token] = React.useState(() => params.get("token"));
  React.useEffect(() => {
    if (token) window.history.replaceState(null, "", window.location.pathname);
  }, [token]);

  const info = useInvitationInfo(token);
  const accept = useAcceptInvitation();
  const form = useForm<AcceptInviteValues>({ resolver: zodResolver(acceptInviteSchema) });
  const { errors } = form.formState;

  const onSubmit = form.handleSubmit(({ name, password }) =>
    token &&
    accept.mutate(
      { token, name, password },
      {
        onSuccess: (data) => {
          toast.success(`Welcome to ${data.tenant.name}`);
          router.replace("/dashboard");
        },
      },
    ),
  );

  if (!token || info.isError) {
    return (
      <Card className="border-0 shadow-none sm:border sm:shadow-sm">
        <CardHeader className="items-center text-center">
          <MailX className="mb-2 h-10 w-10 text-destructive" />
          <CardTitle className="text-2xl">This invitation didn&apos;t work</CardTitle>
          <CardDescription>
            {token
              ? apiErrorMessage(info.error)
              : "The invitation link is incomplete. Open it straight from the email."}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex justify-center">
          <Button asChild variant="outline">
            <Link href="/login">Go to sign in</Link>
          </Button>
        </CardContent>
      </Card>
    );
  }

  if (info.isLoading || !info.data) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 text-muted-foreground" role="status">
        <Loader2 className="h-8 w-8 animate-spin" />
        <p>Opening your invitation…</p>
      </div>
    );
  }

  const invitation = info.data;
  return (
    <Card className="border-0 shadow-none sm:border sm:shadow-sm">
      <CardHeader>
        <Users className="mb-2 h-8 w-8 text-primary" />
        <CardTitle className="text-2xl">Join {invitation.workspace_name}</CardTitle>
        <CardDescription>
          {invitation.invited_by ?? "An administrator"} invited <strong>{invitation.email}</strong> as{" "}
          <strong>{ROLE_INFO[invitation.role].label}</strong>. {ROLE_INFO[invitation.role].description}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="space-y-4" noValidate>
          <div className="space-y-2">
            <Label htmlFor="name">Your name</Label>
            <Input id="name" autoComplete="name" autoFocus aria-invalid={Boolean(errors.name)} {...form.register("name")} />
            <FieldError message={errors.name?.message} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">Choose a password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="new-password"
              aria-invalid={Boolean(errors.password)}
              {...form.register("password")}
            />
            <FieldError message={errors.password?.message} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="confirm">Confirm password</Label>
            <Input
              id="confirm"
              type="password"
              autoComplete="new-password"
              aria-invalid={Boolean(errors.confirm)}
              {...form.register("confirm")}
            />
            <FieldError message={errors.confirm?.message} />
          </div>
          {accept.isError && <FieldError message={apiErrorMessage(accept.error)} />}
          <Button type="submit" className="w-full" disabled={accept.isPending}>
            {accept.isPending && <Loader2 className="animate-spin" />}
            Accept and join
          </Button>
        </form>
        <p className="mt-6 text-center text-sm text-muted-foreground">
          Already have an account?{" "}
          <Link href="/login" className="font-medium text-primary hover:underline">
            Sign in
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}

export default function AcceptInvitePage() {
  return (
    <Suspense>
      <AcceptInvite />
    </Suspense>
  );
}
