"use client";

import * as React from "react";
import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { CheckCircle2, Loader2, MailX } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { apiErrorMessage } from "@/lib/api";
import { useResendVerification, useVerifyEmail } from "@/lib/hooks/account";
import { getApiKey } from "@/lib/session";

/** Read the token once and drop it from the address bar and browser history. */
function useLinkToken(): string | null {
  const params = useSearchParams();
  const [token] = React.useState(() => params.get("token"));
  React.useEffect(() => {
    if (token) window.history.replaceState(null, "", window.location.pathname);
  }, [token]);
  return token;
}

function VerifyEmail() {
  const token = useLinkToken();
  const verify = useVerifyEmail();
  const resend = useResendVerification();
  const started = React.useRef(false);
  const [signedIn, setSignedIn] = React.useState(false);

  React.useEffect(() => {
    setSignedIn(Boolean(getApiKey()));
    // Tokens are single use, so never send one twice (React runs effects twice in dev).
    if (token && !started.current) {
      started.current = true;
      verify.mutate(token);
    }
  }, [token, verify]);

  if (verify.isSuccess) {
    return (
      <Card className="border-0 shadow-none sm:border sm:shadow-sm">
        <CardHeader className="items-center text-center">
          <CheckCircle2 className="mb-2 h-10 w-10 text-success" />
          <CardTitle className="text-2xl">Email verified</CardTitle>
          <CardDescription>
            {verify.data.email} is confirmed. You can now create API keys and upload your full catalogue.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex justify-center">
          <Button asChild>
            {signedIn ? (
              <Link href="/dashboard/api-keys">Create your first API key</Link>
            ) : (
              <Link href="/login?next=/dashboard/api-keys">Sign in</Link>
            )}
          </Button>
        </CardContent>
      </Card>
    );
  }

  if (verify.isError || !token) {
    const send = () =>
      resend.mutate(undefined, {
        onSuccess: (data) => toast.success(data.message),
        onError: (error) =>
          toast.error("Could not send the email", {
            description: apiErrorMessage(error),
          }),
      });
    return (
      <Card className="border-0 shadow-none sm:border sm:shadow-sm">
        <CardHeader className="items-center text-center">
          <MailX className="mb-2 h-10 w-10 text-destructive" />
          <CardTitle className="text-2xl">This link didn&apos;t work</CardTitle>
          <CardDescription>
            {token
              ? apiErrorMessage(verify.error)
              : "The verification link is incomplete. Open it straight from the email."}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col items-center gap-3">
          {signedIn ? (
            <Button onClick={send} disabled={resend.isPending || resend.isSuccess}>
              {resend.isPending && <Loader2 className="animate-spin" />}
              {resend.isSuccess ? "New link sent" : "Send a new link"}
            </Button>
          ) : (
            <Button asChild>
              <Link href="/login?next=/dashboard">Sign in to get a new link</Link>
            </Button>
          )}
          <Link href="/dashboard" className="text-sm text-muted-foreground hover:underline">
            Go to the dashboard
          </Link>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col items-center gap-3 py-16 text-muted-foreground" role="status">
      <Loader2 className="h-8 w-8 animate-spin" />
      <p>Verifying your email…</p>
    </div>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense>
      <VerifyEmail />
    </Suspense>
  );
}
