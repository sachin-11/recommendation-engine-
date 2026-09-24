import { Sparkles } from "lucide-react";

import { APP_NAME, cn } from "@/lib/utils";

export function Logo({ className }: { className?: string }) {
  return (
    <div className={cn("flex items-center gap-2 font-semibold tracking-tight", className)}>
      <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm">
        <Sparkles className="h-4 w-4" />
      </span>
      <span>{APP_NAME}</span>
    </div>
  );
}
