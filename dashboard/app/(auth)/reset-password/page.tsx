"use client";

import * as React from "react";
import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { zodResolver } from "@hookform/resolvers/zod";
import { CheckCircle2, Loader2 } from "lucide-react";
import { useForm } from "react-hook-form";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { FieldError, Label } from "@/components/ui/label";
import { apiErrorMessage } from "@/lib/api";
import { useResetPassword } from "@/lib/hooks/account";
import { resetPasswordSchema, type ResetPasswordValues } from "@/lib/validators";

function ResetPassword() {
  const params = useSearchParams();
  // Keep the token in memory only and drop it from the address bar and history.
  const [token] = React.useState(() => params.get("token"));
  React.useEffect(() => {
    if (token) window.history.replaceState(null, "", window.location.pathname);
  }, [token]);

  const reset = useResetPassword();
  const form = useForm<ResetPasswordValues>({
    resolver: zodResolver(resetPasswordSchema),
  });
  const { errors } = form.formState;
  const onSubmit = form.handleSubmit(({ password }) => token && reset.mutate({ token, password }));

  if (reset.isSuccess) {
    return (
      <Card className="border-0 shadow-none sm:border sm:shadow-sm">
        <CardHeader className="items-center text-center">
          <CheckCircle2 className="mb-2 h-10 w-10 text-success" />
          <CardTitle className="text-2xl">Password updated</CardTitle>
          <CardDescription>Every dashboard session was signed out. Your API keys keep working.</CardDescription>
        </CardHeader>
        <CardContent className="flex justify-center">
          <Button asChild>
            <Link href="/login">Sign in</Link>
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-0 shadow-none sm:border sm:shadow-sm">
      <CardHeader>
        <CardTitle className="text-2xl">Choose a new password</CardTitle>
        <CardDescription>Use at least 8 characters. The link works once.</CardDescription>
      </CardHeader>
      <CardContent>
        {!token ? (
          <div className="space-y-4 text-sm">
            <FieldError message="This reset link is incomplete. Open it straight from the email, or request a new one." />
            <Button asChild className="w-full">
              <Link href="/forgot-password">Request a new link</Link>
            </Button>
          </div>
        ) : (
          <form onSubmit={onSubmit} className="space-y-4" noValidate>
            <div className="space-y-2">
              <Label htmlFor="password">New password</Label>
              <Input
                id="password"
                type="password"
                autoComplete="new-password"
                autoFocus
                aria-invalid={Boolean(errors.password)}
                {...form.register("password")}
              />
              <FieldError message={errors.password?.message} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="confirm">Confirm new password</Label>
              <Input
                id="confirm"
                type="password"
                autoComplete="new-password"
                aria-invalid={Boolean(errors.confirm)}
                {...form.register("confirm")}
              />
              <FieldError message={errors.confirm?.message} />
            </div>
            {reset.isError && (
              <div className="space-y-2">
                <FieldError message={apiErrorMessage(reset.error)} />
                <Link href="/forgot-password" className="text-sm font-medium text-primary hover:underline">
                  Request a new link
                </Link>
              </div>
            )}
            <Button type="submit" className="w-full" disabled={reset.isPending}>
              {reset.isPending && <Loader2 className="animate-spin" />}
              Update password
            </Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense>
      <ResetPassword />
    </Suspense>
  );
}
