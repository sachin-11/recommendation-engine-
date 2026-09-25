"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { zodResolver } from "@hookform/resolvers/zod";
import { KeyRound, Loader2, Mail } from "lucide-react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { FieldError, Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { apiErrorMessage } from "@/lib/api";
import { useApiKeyLogin, useLogin } from "@/lib/hooks/account";
import {
  apiKeyLoginSchema,
  type ApiKeyLoginValues,
  loginSchema,
  type LoginValues,
} from "@/lib/validators";

function useAfterLogin() {
  const router = useRouter();
  const next = useSearchParams().get("next");
  // Only same-site paths, never an absolute URL from the query string.
  const target = next && next.startsWith("/dashboard") ? next : "/dashboard";
  return () => router.replace(target);
}

function PasswordForm() {
  const login = useLogin();
  const done = useAfterLogin();
  const form = useForm<LoginValues>({ resolver: zodResolver(loginSchema) });
  const onSubmit = form.handleSubmit((values) =>
    login.mutate(values, {
      onSuccess: (data) => {
        toast.success(`Welcome back, ${data.tenant.name}`);
        done();
      },
    }),
  );
  return (
    <form onSubmit={onSubmit} className="space-y-4" noValidate>
      <div className="space-y-2">
        <Label htmlFor="email">Email</Label>
        <Input
          id="email"
          type="email"
          autoComplete="email"
          placeholder="you@company.com"
          aria-invalid={Boolean(form.formState.errors.email)}
          {...form.register("email")}
        />
        <FieldError message={form.formState.errors.email?.message} />
      </div>
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label htmlFor="password">Password</Label>
          <Link href="/forgot-password" className="text-xs font-medium text-primary hover:underline">
            Forgot password?
          </Link>
        </div>
        <Input
          id="password"
          type="password"
          autoComplete="current-password"
          aria-invalid={Boolean(form.formState.errors.password)}
          {...form.register("password")}
        />
        <FieldError message={form.formState.errors.password?.message} />
      </div>
      {login.isError && <FieldError message={apiErrorMessage(login.error)} />}
      <Button type="submit" className="w-full" disabled={login.isPending}>
        {login.isPending && <Loader2 className="animate-spin" />}
        Sign in
      </Button>
    </form>
  );
}

function ApiKeyForm() {
  const login = useApiKeyLogin();
  const done = useAfterLogin();
  const form = useForm<ApiKeyLoginValues>({ resolver: zodResolver(apiKeyLoginSchema) });
  const onSubmit = form.handleSubmit(({ apiKey }) =>
    login.mutate(apiKey, {
      onSuccess: (tenant) => {
        toast.success(`Signed in as ${tenant.name}`);
        done();
      },
    }),
  );
  return (
    <form onSubmit={onSubmit} className="space-y-4" noValidate>
      <div className="space-y-2">
        <Label htmlFor="apiKey">API key</Label>
        <Input
          id="apiKey"
          type="password"
          autoComplete="off"
          placeholder="reco_…"
          className="font-mono"
          aria-invalid={Boolean(form.formState.errors.apiKey)}
          {...form.register("apiKey")}
        />
        <FieldError message={form.formState.errors.apiKey?.message} />
        <p className="text-xs text-muted-foreground">
          For accounts created through the API without a password. The key is stored in this
          browser only.
        </p>
      </div>
      {login.isError && (
        <FieldError
          message={
            (login.error as { response?: { status?: number } }).response?.status === 401
              ? "That API key is invalid, revoked or expired."
              : apiErrorMessage(login.error)
          }
        />
      )}
      <Button type="submit" className="w-full" disabled={login.isPending}>
        {login.isPending && <Loader2 className="animate-spin" />}
        Sign in with API key
      </Button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <Card className="border-0 shadow-none sm:border sm:shadow-sm">
      <CardHeader>
        <CardTitle className="text-2xl">Sign in</CardTitle>
        <CardDescription>Manage your items, keys and recommendations.</CardDescription>
      </CardHeader>
      <CardContent>
        <Suspense>
          <Tabs defaultValue="password">
            <TabsList className="grid w-full grid-cols-2">
              <TabsTrigger value="password">
                <Mail /> Email
              </TabsTrigger>
              <TabsTrigger value="key">
                <KeyRound /> API key
              </TabsTrigger>
            </TabsList>
            <TabsContent value="password">
              <PasswordForm />
            </TabsContent>
            <TabsContent value="key">
              <ApiKeyForm />
            </TabsContent>
          </Tabs>
        </Suspense>
        <p className="mt-6 text-center text-sm text-muted-foreground">
          New here?{" "}
          <Link href="/register" className="font-medium text-primary hover:underline">
            Create an account
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}
