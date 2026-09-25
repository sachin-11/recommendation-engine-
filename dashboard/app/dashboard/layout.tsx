"use client";

import * as React from "react";
import { usePathname, useRouter } from "next/navigation";

import { VerifyEmailBanner } from "@/components/account/VerifyEmailBanner";
import { Sidebar } from "@/components/layout/Sidebar";
import { TopBar } from "@/components/layout/TopBar";
import { getApiKey, SESSION_EVENT } from "@/lib/session";

/** Client-side guard: the session key lives in localStorage, so the server cannot check it. */
function useRequireSession() {
  const router = useRouter();
  const pathname = usePathname();
  const [ready, setReady] = React.useState(false);
  React.useEffect(() => {
    const check = () => {
      if (getApiKey()) {
        setReady(true);
      } else {
        setReady(false);
        router.replace(`/login?next=${encodeURIComponent(pathname)}`);
      }
    };
    check();
    window.addEventListener(SESSION_EVENT, check);
    window.addEventListener("storage", check); // signed out in another tab
    return () => {
      window.removeEventListener(SESSION_EVENT, check);
      window.removeEventListener("storage", check);
    };
  }, [router, pathname]);
  return ready;
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const ready = useRequireSession();
  if (!ready) return null;
  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6 lg:py-8">
          <VerifyEmailBanner />
          {children}
        </main>
      </div>
    </div>
  );
}
