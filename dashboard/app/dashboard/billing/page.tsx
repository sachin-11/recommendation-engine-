"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertTriangle, Check, CreditCard, ExternalLink, Info, Loader2, Sparkles, X } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/misc";
import { Skeleton } from "@/components/ui/skeleton";
import { toastApiError } from "@/lib/api";
import { useMe } from "@/lib/hooks/account";
import { useBilling, useCheckout, usePortal, useSyncBilling } from "@/lib/hooks/billing";
import { can, needsRole } from "@/lib/roles";
import { formatDate, formatNumber } from "@/lib/utils";
import type { Allowance, Billing, Plan } from "@/types";

const PLAN_NAMES: Record<Plan, string> = { FREE: "Free", PRO: "Pro" };

function money(amount: number, currency: string) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 0 }).format(
    amount / 100,
  );
}

function limit(value: number | null) {
  return value == null ? "Unlimited" : formatNumber(value);
}

/** A used/limit meter. Past 90% it turns to the warning colour and says so in words. */
function UsageMeter({ label, used, cap }: { label: string; used: number; cap: number | null }) {
  const share = cap ? Math.min(1, used / cap) : 0;
  const high = cap != null && share >= 0.9;
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-2 text-sm">
        <span>{label}</span>
        <span className="tabular-nums text-muted-foreground">
          {formatNumber(used)} / {limit(cap)}
        </span>
      </div>
      <div
        className="h-2 w-full overflow-hidden rounded-full bg-muted"
        role="meter"
        aria-label={label}
        aria-valuenow={used}
        aria-valuemin={0}
        aria-valuemax={cap ?? undefined}
      >
        {cap != null && (
          <div
            className={high ? "h-full rounded-full bg-warning" : "h-full rounded-full bg-primary"}
            style={{ width: `${Math.max(share * 100, used ? 2 : 0)}%` }}
          />
        )}
      </div>
      {high && <p className="text-xs text-warning">{share >= 1 ? "Limit reached." : "Nearly at the limit."}</p>}
    </div>
  );
}

function SubscriptionNote({ billing }: { billing: Billing }) {
  const end = billing.current_period_end ? formatDate(billing.current_period_end, false) : null;
  const status = billing.subscription_status;
  if (billing.subscribed_plan === "FREE" || !status) return null;
  if (status === "past_due")
    return (
      <p className="flex items-center gap-1.5 text-sm text-warning">
        <AlertTriangle className="h-4 w-4" /> The last payment failed. Stripe will retry; update the card under Manage
        billing to keep Pro.
      </p>
    );
  if (billing.plan === "FREE")
    return <p className="text-sm text-muted-foreground">Your Pro subscription ended ({status}).</p>;
  if (billing.cancel_at_period_end && end)
    return <p className="text-sm text-muted-foreground">Cancelled: Pro stays until {end}, then the Free plan applies.</p>;
  return end ? <p className="text-sm text-muted-foreground">Renews on {end}.</p> : null;
}

const FEATURES: { label: string; value: (a: Allowance) => string | boolean }[] = [
  { label: "Items", value: (a) => limit(a.max_items) },
  { label: "Recommendations a month", value: (a) => limit(a.monthly_queries) },
  { label: "Vector, hybrid and feedback ranking", value: () => true },
  { label: "LLM re-ranking with reasons", value: (a) => a.llm_features },
  { label: "Ask in plain language, streamed answers", value: (a) => a.llm_features },
];

function PlanCard({
  billing,
  plan,
  interval,
  canManage,
}: {
  billing: Billing;
  plan: Plan;
  interval: "month" | "year";
  canManage: boolean;
}) {
  const allowance = billing.plans.find((p) => p.plan === plan)?.allowance;
  const checkout = useCheckout();
  // With billing off no plan applies: every workspace has everything.
  const current = billing.enabled && billing.plan === plan;
  const price = plan === "PRO" ? billing.pro_prices.find((p) => p.interval === interval) : null;
  // Why upgrading is not possible right now, said on the page rather than only on hover.
  const blocked = !billing.enabled
    ? "Upgrading is available once billing is turned on for this server."
    : !price
      ? "The Pro price could not be loaded from Stripe. Try again shortly."
      : !canManage
        ? "Only the workspace owner or an admin can upgrade."
        : null;
  if (!allowance) return null;
  return (
    <Card className={current ? "border-primary" : undefined}>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">{PLAN_NAMES[plan]}</CardTitle>
          {current && <Badge>Current plan</Badge>}
        </div>
        <CardDescription>
          {plan === "FREE" ? (
            <span className="text-2xl font-semibold text-foreground">$0</span>
          ) : price ? (
            <>
              <span className="text-2xl font-semibold text-foreground">{money(price.unit_amount, price.currency)}</span>{" "}
              per {interval}
            </>
          ) : (
            billing.enabled ? "Price unavailable" : "Paid plan"
          )}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="space-y-2 text-sm">
          {FEATURES.map(({ label, value }) => {
            const v = value(allowance);
            return (
              <li key={label} className="flex items-center gap-2">
                {v === false ? (
                  <X className="h-4 w-4 shrink-0 text-muted-foreground" aria-label="Not included" />
                ) : (
                  <Check className="h-4 w-4 shrink-0 text-primary" aria-label="Included" />
                )}
                <span className={v === false ? "text-muted-foreground" : undefined}>
                  {typeof v === "string" ? `${v} ${label.toLowerCase()}` : label}
                </span>
              </li>
            );
          })}
        </ul>
      </CardContent>
      {plan === "PRO" && !current && (
        <CardFooter className="flex-col items-stretch gap-2">
          {billing.enabled && (
            <Button
              className="w-full"
              onClick={() => checkout.mutate(interval, { onError: (e) => toastApiError(e, "Could not open checkout") })}
              disabled={Boolean(blocked) || checkout.isPending}
            >
              {checkout.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />}
              Upgrade to Pro
            </Button>
          )}
          {blocked && <p className="text-center text-xs text-muted-foreground">{blocked}</p>}
        </CardFooter>
      )}
    </Card>
  );
}

/** Back from Stripe Checkout: read the subscription now, and again for a little while
 * if the webhook has not landed yet. */
function useCheckoutReturn(billing: Billing | undefined) {
  const params = useSearchParams();
  const router = useRouter();
  const sync = useSyncBilling();
  const outcome = params.get("checkout");
  const tries = React.useRef(0);

  React.useEffect(() => {
    if (outcome === "cancelled") {
      toast("Checkout cancelled", { description: "Nothing was charged." });
      router.replace("/dashboard/billing");
    }
  }, [outcome, router]);

  React.useEffect(() => {
    if (outcome !== "success" || !billing || sync.isPending) return;
    if (billing.plan === "PRO") {
      toast.success("Welcome to Pro", { description: "LLM re-ranking and Ask are now available." });
      router.replace("/dashboard/billing");
      return;
    }
    if (tries.current >= 6) return;
    const timer = setTimeout(() => {
      tries.current += 1;
      sync.mutate();
    }, tries.current === 0 ? 0 : 2000);
    return () => clearTimeout(timer);
  }, [outcome, billing, sync, router]);

  return outcome === "success" && billing?.plan !== "PRO";
}

function BillingPage() {
  const { data: me } = useMe();
  const { data: billing, isLoading } = useBilling();
  const portal = usePortal();
  const [interval, setBillingInterval] = React.useState<"month" | "year">("month");
  const confirming = useCheckoutReturn(billing);
  const canManage = can(me, "ADMIN");

  if (isLoading || !billing) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-40" />
        <Skeleton className="h-72" />
      </div>
    );
  }

  const yearly = billing.pro_prices.find((p) => p.interval === "year");
  const monthly = billing.pro_prices.find((p) => p.interval === "month");
  const saving = yearly && monthly ? 1 - yearly.unit_amount / (monthly.unit_amount * 12) : 0;

  return (
    <div className="space-y-6">
      {!billing.enabled && (
        <div className="flex items-start gap-3 rounded-lg border p-4 text-sm">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
          <p>
            Billing is turned off on this server, so nothing is limited by plan and every feature is on. The plans below
            apply once <code className="font-mono">BILLING_ENABLED</code> is set.
          </p>
        </div>
      )}
      {confirming && (
        <div className="flex items-center gap-3 rounded-lg border p-4 text-sm" role="status">
          <Loader2 className="h-4 w-4 animate-spin text-primary" /> Confirming your payment with Stripe…
        </div>
      )}

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <CreditCard className="h-4 w-4" />{" "}
              {billing.enabled ? `${PLAN_NAMES[billing.plan]} plan` : "All features, no plan limits"}
            </CardTitle>
            {billing.subscribed_plan === "PRO" && (
              <Button
                variant="outline"
                onClick={() => portal.mutate(undefined, { onError: (e) => toastApiError(e, "Could not open billing") })}
                disabled={!canManage || portal.isPending}
                title={canManage ? undefined : needsRole("ADMIN")}
              >
                {portal.isPending ? <Loader2 className="animate-spin" /> : <ExternalLink />}
                Manage billing
              </Button>
            )}
          </div>
          <CardDescription>
            <SubscriptionNote billing={billing} />
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-5 sm:grid-cols-2">
          <UsageMeter label="Items" used={billing.usage.items} cap={billing.allowance.max_items} />
          <UsageMeter
            label="Recommendations this month"
            used={billing.usage.queries_this_month}
            cap={billing.allowance.monthly_queries}
          />
        </CardContent>
      </Card>

      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">Plans</h2>
          <div className="inline-flex rounded-lg border p-1" role="group" aria-label="Billing period">
            {(["month", "year"] as const).map((i) => (
              <Button
                key={i}
                size="sm"
                variant={interval === i ? "secondary" : "ghost"}
                aria-pressed={interval === i}
                onClick={() => setBillingInterval(i)}
              >
                {i === "month" ? "Monthly" : "Yearly"}
                {i === "year" && saving > 0.01 && (
                  <span className="text-xs text-muted-foreground">save {Math.round(saving * 100)}%</span>
                )}
              </Button>
            ))}
          </div>
        </div>
        <div className="grid gap-6 md:grid-cols-2">
          {(["FREE", "PRO"] as const).map((plan) => (
            <PlanCard key={plan} billing={billing} plan={plan} interval={interval} canManage={canManage} />
          ))}
        </div>
        <p className="text-xs text-muted-foreground">
          Payments are handled by Stripe; card details never reach this app. Test mode: use card 4242 4242 4242 4242, any
          future date and any CVC.
        </p>
      </div>
    </div>
  );
}

export default function BillingRoute() {
  return (
    <>
      <PageHeader title="Billing" description="Your plan, what you have used, and upgrading to Pro." />
      <React.Suspense fallback={<Skeleton className="h-72" />}>
        <BillingPage />
      </React.Suspense>
    </>
  );
}
