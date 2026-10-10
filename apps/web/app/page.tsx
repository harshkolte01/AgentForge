import { Sparkles } from "lucide-react";
import Link from "next/link";

import { SystemHealth } from "@/components/system-health";

export default function Home() {
  return (
    <main className="flex min-h-svh items-center justify-center bg-muted/40 px-6 py-16">
      <div className="w-full max-w-xl space-y-8">
        <header className="space-y-4">
          <div className="flex size-12 items-center justify-center rounded-xl bg-primary text-primary-foreground">
            <Sparkles aria-hidden="true" className="size-6" />
          </div>
          <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
            AgentForge AI
          </h1>
          <p className="text-base leading-relaxed text-muted-foreground">
            Your workspace for coding, research, and problem solving.
          </p>
        </header>

        <SystemHealth />
        <Link href="/chat" className="inline-flex rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground outline-none hover:bg-primary/80 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2">Open workspace</Link>
      </div>
    </main>
  );
}
