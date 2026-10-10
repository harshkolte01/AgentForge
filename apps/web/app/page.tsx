import { Activity, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

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

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Activity aria-hidden="true" className="size-4" />
              <h2>System health</h2>
            </CardTitle>
            <CardDescription>Service health status will appear here.</CardDescription>
          </CardHeader>
          <CardContent>
            <Badge variant="outline" className="gap-2">
              <span aria-hidden="true" className="size-2 rounded-full bg-amber-500" />
              Not checked
            </Badge>
            <p className="mt-3 text-sm text-muted-foreground">
              Waiting for the health check connection.
            </p>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
