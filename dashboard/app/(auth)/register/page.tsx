"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowLeft, ArrowRight, Check, Loader2, MailCheck } from "lucide-react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { configErrors, DomainConfigEditor } from "@/components/onboarding/DomainConfigEditor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { JsonView } from "@/components/ui/json-view";
import { FieldError, Label } from "@/components/ui/label";
import { apiErrorMessage } from "@/lib/api";
import { DOMAIN_PRESETS, exampleItemFor, presetFor } from "@/lib/domains";
import { useRegister, useResendVerification } from "@/lib/hooks/account";
import { cn } from "@/lib/utils";
import { accountSchema, type AccountValues } from "@/lib/validators";
import type { DomainConfig, DomainType } from "@/types";

const STEPS = ["Account", "Domain", "Configure", "Verify email"];

function Stepper({ step }: { step: number }) {
  return (
    <ol className="mb-8 flex items-center gap-2" aria-label="Progress">
      {STEPS.map((label, index) => {
        const done = index < step;
        const current = index === step;
        return (
          <li key={label} className="flex flex-1 items-center gap-2">
            <span
              className={cn(
                "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold",
                done && "border-primary bg-primary text-primary-foreground",
                current && "border-primary text-primary",
                !done && !current && "text-muted-foreground",
              )}
              aria-current={current ? "step" : undefined}
            >
              {done ? <Check className="h-3.5 w-3.5" /> : index + 1}
            </span>
            <span
              className={cn(
                "hidden text-sm sm:inline",
                current ? "font-medium" : "text-muted-foreground",
              )}
            >
              {label}
            </span>
            {index < STEPS.length - 1 && <span className="h-px flex-1 bg-border" />}
          </li>
        );
      })}
    </ol>
  );
}

function AccountStep({
  defaults,
  onNext,
}: {
  defaults?: AccountValues;
  onNext: (values: AccountValues) => void;
}) {
  const form = useForm<AccountValues>({ resolver: zodResolver(accountSchema), defaultValues: defaults });
  const { errors } = form.formState;
  return (
    <form onSubmit={form.handleSubmit(onNext)} className="space-y-4" noValidate>
      <div className="space-y-2">
        <Label htmlFor="owner_name">Your name</Label>
        <Input
          id="owner_name"
          autoComplete="name"
          placeholder="Priya Sharma"
          aria-invalid={Boolean(errors.owner_name)}
          {...form.register("owner_name")}
        />
        <FieldError message={errors.owner_name?.message} />
      </div>
      <div className="space-y-2">
        <Label htmlFor="name">Business name</Label>
        <Input id="name" placeholder="Acme Hiring" aria-invalid={Boolean(errors.name)} {...form.register("name")} />
        <FieldError message={errors.name?.message} />
      </div>
      <div className="space-y-2">
        <Label htmlFor="email">Work email</Label>
        <Input
          id="email"
          type="email"
          autoComplete="email"
          placeholder="you@company.com"
          aria-invalid={Boolean(errors.email)}
          {...form.register("email")}
        />
        <FieldError message={errors.email?.message} />
      </div>
      <div className="space-y-2">
        <Label htmlFor="password">Password</Label>
        <Input
          id="password"
          type="password"
          autoComplete="new-password"
          aria-invalid={Boolean(errors.password)}
          {...form.register("password")}
        />
        <p className="text-xs text-muted-foreground">At least 8 characters.</p>
        <FieldError message={errors.password?.message} />
      </div>
      <Button type="submit" className="w-full">
        Continue <ArrowRight />
      </Button>
    </form>
  );
}

function DomainStep({
  selected,
  onSelect,
}: {
  selected: DomainType;
  onSelect: (type: DomainType) => void;
}) {
  const preset = presetFor(selected);
  return (
    <div className="space-y-5">
      <div role="radiogroup" aria-label="Domain" className="grid gap-3 sm:grid-cols-2">
        {DOMAIN_PRESETS.map((option) => {
          const active = option.type === selected;
          const Icon = option.icon;
          return (
            <button
              key={option.type}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => onSelect(option.type)}
              className={cn(
                "flex items-start gap-3 rounded-lg border p-4 text-left transition-colors hover:border-primary/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                active && "border-primary bg-primary/5 ring-1 ring-primary",
                option.type === "CUSTOM" && "sm:col-span-2",
              )}
            >
              <span
                className={cn(
                  "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-muted",
                  active && "bg-primary text-primary-foreground",
                )}
              >
                <Icon className="h-5 w-5" />
              </span>
              <span>
                <span className="block font-medium">{option.label}</span>
                <span className="block text-sm text-muted-foreground">{option.useCase}</span>
              </span>
            </button>
          );
        })}
      </div>
      <div className="rounded-lg border bg-muted/30 p-4">
        <p className="mb-3 text-sm font-medium">What we will track for each {preset.config.item_label}</p>
        <div className="space-y-2 text-sm">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="w-24 shrink-0 text-muted-foreground">Embedded</span>
            {preset.config.searchable_fields.map((field) => (
              <Badge key={field} variant={field === preset.config.primary_embedding_field ? "info" : "secondary"}>
                {field}
              </Badge>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="w-24 shrink-0 text-muted-foreground">Filters</span>
            {preset.config.filter_fields.map((field) => (
              <Badge key={field} variant="outline">
                {field}
              </Badge>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function VerifyStep({ email, verificationRequired }: { email: string; verificationRequired: boolean }) {
  const router = useRouter();
  const resend = useResendVerification();
  return (
    <div className="space-y-5">
      <div className="flex items-start gap-3 rounded-lg border bg-muted/30 p-4 text-sm">
        <MailCheck className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
        <div className="space-y-2">
          <p>
            We sent a verification link to <strong>{email}</strong>. Open it to confirm your address.
          </p>
          {verificationRequired && (
            <p className="text-muted-foreground">
              Until then you can explore the dashboard and try a few uploads; API keys for your
              application unlock once the email is verified.
            </p>
          )}
        </div>
      </div>
      <div className="flex flex-col gap-2 sm:flex-row">
        <Button
          variant="outline"
          className="sm:flex-1"
          disabled={resend.isPending || resend.isSuccess}
          onClick={() =>
            resend.mutate(undefined, {
              onSuccess: (data) => toast.success(data.message),
              onError: (error) => toast.error("Could not send the email", { description: apiErrorMessage(error) }),
            })
          }
        >
          {resend.isPending && <Loader2 className="animate-spin" />}
          {resend.isSuccess ? "Email sent" : "Resend email"}
        </Button>
        <Button className="sm:flex-1" onClick={() => router.push("/dashboard")}>
          Go to Dashboard <ArrowRight />
        </Button>
      </div>
    </div>
  );
}

export default function RegisterPage() {
  const [step, setStep] = React.useState(0);
  const [account, setAccount] = React.useState<AccountValues>();
  const [domainType, setDomainType] = React.useState<DomainType>("HR");
  const [config, setConfig] = React.useState<DomainConfig>(presetFor("HR").config);
  const [registered, setRegistered] = React.useState<{ email: string; verificationRequired: boolean }>();
  const register = useRegister();

  const chooseDomain = (type: DomainType) => {
    setDomainType(type);
    setConfig(presetFor(type).config);
  };

  const submit = () => {
    if (!account) return;
    register.mutate(
      { ...account, domain_type: domainType, domain_config: config },
      {
        onSuccess: (data) => {
          setRegistered({ email: data.tenant.email, verificationRequired: data.verification_required });
          setStep(3);
          toast.success("Account created");
        },
      },
    );
  };

  const configInvalid = Object.keys(configErrors(config)).length > 0;
  const titles = [
    ["Create your account", "Start with your business details."],
    ["What will you recommend?", "Pick the closest domain. You can change every field next."],
    ["Configure your items", "Tell us which fields describe an item and which ones to filter on."],
    ["Check your inbox", "One last step: confirm your email address."],
  ];

  return (
    <Card className="border-0 shadow-none sm:border sm:shadow-sm">
      <CardHeader>
        <Stepper step={step} />
        <CardTitle className="text-2xl">{titles[step][0]}</CardTitle>
        <CardDescription>{titles[step][1]}</CardDescription>
      </CardHeader>
      <CardContent>
        {step === 0 && (
          <AccountStep
            defaults={account}
            onNext={(values) => {
              setAccount(values);
              setStep(1);
            }}
          />
        )}

        {step === 1 && (
          <div className="space-y-5">
            <DomainStep selected={domainType} onSelect={chooseDomain} />
            <div className="flex justify-between">
              <Button variant="ghost" onClick={() => setStep(0)}>
                <ArrowLeft /> Back
              </Button>
              <Button onClick={() => setStep(2)}>
                Continue <ArrowRight />
              </Button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="space-y-5">
            <DomainConfigEditor value={config} onChange={setConfig} />
            <div className="space-y-2">
              <p className="text-sm font-medium">An item should look like this</p>
              <JsonView value={exampleItemFor(config, domainType)} className="max-h-60" />
            </div>
            {register.isError && <FieldError message={apiErrorMessage(register.error)} />}
            <div className="flex justify-between">
              <Button variant="ghost" onClick={() => setStep(1)} disabled={register.isPending}>
                <ArrowLeft /> Back
              </Button>
              <Button onClick={submit} disabled={configInvalid || register.isPending}>
                {register.isPending && <Loader2 className="animate-spin" />}
                Create account
              </Button>
            </div>
          </div>
        )}

        {step === 3 && registered && (
          <VerifyStep email={registered.email} verificationRequired={registered.verificationRequired} />
        )}

        {step < 3 && (
          <p className="mt-6 text-center text-sm text-muted-foreground">
            Already have an account?{" "}
            <Link href="/login" className="font-medium text-primary hover:underline">
              Sign in
            </Link>
          </p>
        )}
      </CardContent>
    </Card>
  );
}
