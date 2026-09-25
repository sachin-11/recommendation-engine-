"use client";

import Link from "next/link";
import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowLeft, Loader2, MailCheck } from "lucide-react";
import { useForm } from "react-hook-form";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { FieldError, Label } from "@/components/ui/label";
import { apiErrorMessage } from "@/lib/api";
import { useForgotPassword } from "@/lib/hooks/account";
import { forgotPasswordSchema, type ForgotPasswordValues } from "@/lib/validators";

function BackToSignIn() {
  return (
    <Link href="/login" className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline">
      <ArrowLeft className="h-4 w-4" /> Back to sign in
    </Link>
  );
}

export default function ForgotPasswordPage() {
  const forgot = useForgotPassword();
  const form = useForm<ForgotPasswordValues>({
    resolver: zodResolver(forgotPasswordSchema),
  });
  const onSubmit = form.handleSubmit(({ email }) => forgot.mutate(email));

  if (forgot.isSuccess) {
    return (
      <Card className="border-0 shadow-none sm:border sm:shadow-sm">
        <CardHeader className="items-center text-center">
          <MailCheck className="mb-2 h-10 w-10 text-primary" />
          <CardTitle className="text-2xl">Check your inbox</CardTitle>
          <CardDescription>{forgot.data.message}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col items-center gap-3 text-center text-sm text-muted-foreground">
          <p>Nothing after a few minutes? Check spam, or try again with the email you signed up with.</p>
          <BackToSignIn />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-0 shadow-none sm:border sm:shadow-sm">
      <CardHeader>
        <CardTitle className="text-2xl">Reset your password</CardTitle>
        <CardDescription>
          Enter your account email and we&apos;ll send you a link to choose a new password.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="space-y-4" noValidate>
          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              placeholder="you@company.com"
              autoFocus
              aria-invalid={Boolean(form.formState.errors.email)}
              {...form.register("email")}
            />
            <FieldError message={form.formState.errors.email?.message} />
          </div>
          {forgot.isError && <FieldError message={apiErrorMessage(forgot.error)} />}
          <Button type="submit" className="w-full" disabled={forgot.isPending}>
            {forgot.isPending && <Loader2 className="animate-spin" />}
            Send reset link
          </Button>
        </form>
        <div className="mt-6 text-center">
          <BackToSignIn />
        </div>
      </CardContent>
    </Card>
  );
}
