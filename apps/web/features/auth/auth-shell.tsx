import Link from "next/link";
import type { ReactNode } from "react";
import { BrandMark } from "@/components/brand-mark";
import { routes } from "@/lib/routes";

export function AuthShell({ children }: { children: ReactNode }) {
  return <div data-public className="dark flex min-h-svh flex-col bg-background text-foreground">
    <main id="main" className="flex flex-1 items-center justify-center px-6 py-16 sm:py-24">
      <div className="w-full max-w-[390px]">
        <Link href={routes.home} aria-label="AgentForge, home" className="mb-10 inline-flex min-h-11 items-center gap-3 rounded text-sm font-medium"><BrandMark className="size-8" />AgentForge</Link>
        {children}
      </div>
    </main>
  </div>;
}
