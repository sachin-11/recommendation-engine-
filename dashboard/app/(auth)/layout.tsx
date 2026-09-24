import { CheckCircle2 } from "lucide-react";

import { Logo } from "@/components/layout/Logo";

const POINTS = [
  "Works for jobs, dishes, products, courses, or your own items",
  "Upload JSON or CSV; embeddings are created for you",
  "Recommend by text, by similar item, or by profile",
];

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-screen lg:grid-cols-[1fr_1.1fr]">
      <aside className="relative hidden overflow-hidden bg-primary p-10 text-primary-foreground lg:flex lg:flex-col lg:justify-between">
        <div
          className="pointer-events-none absolute inset-0 opacity-20"
          style={{
            backgroundImage:
              "radial-gradient(circle at 20% 20%, white 0, transparent 35%), radial-gradient(circle at 80% 70%, white 0, transparent 30%)",
          }}
        />
        <Logo className="relative [&>span:first-child]:bg-primary-foreground [&>span:first-child]:text-primary" />
        <div className="relative space-y-6">
          <h2 className="text-3xl font-semibold leading-tight">
            Recommendations for any catalogue,
            <br />
            without building an ML team.
          </h2>
          <ul className="space-y-3 text-sm text-primary-foreground/90">
            {POINTS.map((point) => (
              <li key={point} className="flex items-start gap-2">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                {point}
              </li>
            ))}
          </ul>
        </div>
        <p className="relative text-xs text-primary-foreground/70">
          Embeddings by OpenAI · Vector search by Pinecone
        </p>
      </aside>
      <main className="flex items-center justify-center px-4 py-10 sm:px-8">
        <div className="w-full max-w-xl">
          <Logo className="mb-8 lg:hidden" />
          {children}
        </div>
      </main>
    </div>
  );
}
