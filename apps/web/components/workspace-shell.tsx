import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowLeft, Sparkles } from "lucide-react";

export function WorkspaceShell({ children, back = false }: { children: ReactNode; back?: boolean }) {
  return (
    <div className="min-h-svh bg-background">
      <header className="border-b bg-muted/30">
        <nav aria-label="Main navigation" className="mx-auto flex min-h-16 max-w-7xl flex-wrap items-center justify-between gap-4 px-5 py-3 sm:px-8">
          <Link href="/workspace" className="flex items-center gap-2 text-sm font-semibold focus-visible:outline-2 focus-visible:outline-offset-4">
            <Sparkles aria-hidden="true" className="size-4" /> AgentForge AI
          </Link>
          <div className="flex gap-5 text-sm">
            {back ? <Link href="/workspace" className="flex items-center gap-2 underline-offset-4 hover:underline focus-visible:outline-2"><ArrowLeft aria-hidden="true" className="size-4" />Workspace</Link> : null}
            <Link href="/" className="text-muted-foreground underline-offset-4 hover:text-foreground hover:underline focus-visible:outline-2">System health</Link>
          </div>
        </nav>
      </header>
      <main className="mx-auto max-w-7xl px-5 py-8 sm:px-8 sm:py-12">{children}</main>
    </div>
  );
}
