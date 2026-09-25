"use client";

import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { useRouter } from "next/navigation";
import { LogOut, Menu, Monitor, Moon, Sun, X } from "lucide-react";
import { useTheme } from "next-themes";

import { SidebarNav } from "@/components/layout/Sidebar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { presetFor } from "@/lib/domains";
import { useLogout, useMe } from "@/lib/hooks/account";
import { ROLE_INFO } from "@/lib/roles";

function MobileNav() {
  const [open, setOpen] = React.useState(false);
  return (
    <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
      <DialogPrimitive.Trigger asChild>
        <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Open menu">
          <Menu />
        </Button>
      </DialogPrimitive.Trigger>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/50 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content className="fixed inset-y-0 left-0 z-50 w-64 border-r bg-background shadow-lg data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:slide-out-to-left data-[state=open]:slide-in-from-left">
          <DialogPrimitive.Title className="sr-only">Navigation</DialogPrimitive.Title>
          <DialogPrimitive.Description className="sr-only">Dashboard pages</DialogPrimitive.Description>
          <DialogPrimitive.Close className="absolute right-3 top-5 rounded-sm opacity-70 hover:opacity-100" aria-label="Close menu">
            <X className="h-4 w-4" />
          </DialogPrimitive.Close>
          <SidebarNav onNavigate={() => setOpen(false)} />
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);
  const next = theme === "light" ? "dark" : theme === "dark" ? "system" : "light";
  const Icon = !mounted ? Sun : theme === "light" ? Sun : theme === "dark" ? Moon : Monitor;
  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={() => setTheme(next)}
      aria-label={`Theme: ${mounted ? theme : "system"}. Switch to ${next}`}
      title={`Theme: ${mounted ? theme : "system"}`}
    >
      <Icon />
    </Button>
  );
}

export function TopBar() {
  const { data: me, isLoading } = useMe();
  const logout = useLogout();
  const router = useRouter();
  const person = me?.user?.name ?? me?.name ?? "?";
  const initials = person
    .split(/\s+/)
    .map((word) => word[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b bg-background/85 px-4 backdrop-blur sm:px-6">
      <MobileNav />
      <div className="min-w-0 flex-1">
        {isLoading ? (
          <Skeleton className="h-5 w-40" />
        ) : (
          me && (
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate font-medium">{me.name}</span>
              <Badge variant="secondary" className="hidden sm:inline-flex">
                {presetFor(me.domain_type).label}
              </Badge>
            </div>
          )
        )}
      </div>
      <ThemeToggle />
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" className="rounded-full" aria-label="Account menu">
            <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/15 text-xs font-semibold text-primary">
              {initials}
            </span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56">
          <DropdownMenuLabel>
            <p className="truncate font-medium">{me?.user?.name ?? "Signed in with an API key"}</p>
            <p className="truncate text-xs text-muted-foreground">{me?.user?.email ?? me?.email}</p>
            {me && (
              <p className="mt-1 text-xs font-normal text-muted-foreground">
                {ROLE_INFO[me.role].label} · {me.name}
              </p>
            )}
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onSelect={() =>
              logout.mutate(undefined, { onSettled: () => router.replace("/login") })
            }
          >
            <LogOut /> Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  );
}
