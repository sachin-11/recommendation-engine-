"use client";

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, MailWarning } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { apiErrorMessage } from "@/lib/api";
import { keys, useMe, useResendVerification } from "@/lib/hooks/account";

const COOLDOWN_SECONDS = 60;

/** Shown across the dashboard until the account email is verified. */
export function VerifyEmailBanner() {
  const { data: me } = useMe();
  const resend = useResendVerification();
  const queryClient = useQueryClient();
  const [cooldown, setCooldown] = React.useState(0);

  // The link is usually opened in another tab; pick that up when the user comes back.
  React.useEffect(() => {
    if (!me || me.email_verified) return;
    const refresh = () => queryClient.invalidateQueries({ queryKey: keys.me });
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [me, queryClient]);

  React.useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  if (!me || me.email_verified) return null;

  const send = () =>
    resend.mutate(undefined, {
      onSuccess: (data) => {
        toast.success(data.message);
        setCooldown(COOLDOWN_SECONDS);
      },
      onError: (error) =>
        toast.error("Could not send the email", {
          description: apiErrorMessage(error),
        }),
    });

  return (
    <div
      role="status"
      className="mb-6 flex flex-col gap-3 rounded-lg border border-warning/40 bg-warning/10 p-4 text-sm sm:flex-row sm:items-center"
    >
      <MailWarning className="h-5 w-5 shrink-0 text-warning" />
      <p className="flex-1">
        <strong>Verify your email to unlock API keys.</strong> We sent a link to{" "}
        <span className="font-medium">{me.email}</span>. Until then you can use the dashboard with a small daily upload
        limit.
      </p>
      <Button variant="outline" size="sm" onClick={send} disabled={resend.isPending || cooldown > 0}>
        {resend.isPending && <Loader2 className="animate-spin" />}
        {cooldown > 0 ? `Resend in ${cooldown}s` : "Resend email"}
      </Button>
    </div>
  );
}
