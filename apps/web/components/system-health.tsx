"use client";

import { useEffect, useState } from "react";
import { Activity } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

type LiveResponse = {
  status: "ok";
};

type CheckResult = {
  status: "ok" | "error" | "not_configured" | "not_observed";
  detail?: string | null;
};

type ReadyResponse = {
  status: "ready" | "degraded";
  checks: {
    database: CheckResult;
    checkpointer: CheckResult;
    worker: CheckResult;
  };
};

type HealthState =
  | { kind: "loading" }
  | { kind: "unreachable"; message: string }
  | { kind: "ok"; live: LiveResponse; ready: ReadyResponse; readyHttp: number };

async function readJson<T>(path: string): Promise<{ httpStatus: number; body: T }> {
  const response = await fetch(path, { cache: "no-store" });
  const body = (await response.json()) as T;
  return { httpStatus: response.status, body };
}

export function SystemHealth() {
  const [state, setState] = useState<HealthState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [live, ready] = await Promise.all([
          readJson<LiveResponse>("/api/v1/health/live"),
          readJson<ReadyResponse>("/api/v1/health/ready"),
        ]);
        if (cancelled) {
          return;
        }
        if (live.body.status !== "ok") {
          setState({ kind: "unreachable", message: "Live probe did not return ok." });
          return;
        }
        setState({
          kind: "ok",
          live: live.body,
          ready: ready.body,
          readyHttp: ready.httpStatus,
        });
      } catch {
        if (!cancelled) {
          setState({
            kind: "unreachable",
            message: "Could not reach the API through /api. Is FastAPI running on port 8000?",
          });
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity aria-hidden="true" className="size-4" />
          <h2>System health</h2>
        </CardTitle>
        <CardDescription>
          Live and ready probes via the Next.js <code>/api</code> rewrite.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {state.kind === "loading" ? (
          <>
            <Badge variant="outline" className="gap-2">
              <span aria-hidden="true" className="size-2 rounded-full bg-amber-500" />
              Checking
            </Badge>
            <p className="text-sm text-muted-foreground">
              Requesting <code>/api/v1/health/live</code>.
            </p>
          </>
        ) : null}

        {state.kind === "unreachable" ? (
          <>
            <Badge variant="destructive" className="gap-2">
              <span aria-hidden="true" className="size-2 rounded-full bg-destructive" />
              Unreachable
            </Badge>
            <p className="text-sm text-muted-foreground">{state.message}</p>
          </>
        ) : null}

        {state.kind === "ok" ? (
          <>
            <div className="flex flex-wrap gap-2">
              <Badge variant="secondary" className="gap-2">
                <span aria-hidden="true" className="size-2 rounded-full bg-emerald-500" />
                Live
              </Badge>
              <Badge
                variant={state.ready.status === "ready" ? "secondary" : "destructive"}
                className="gap-2"
              >
                <span
                  aria-hidden="true"
                  className={`size-2 rounded-full ${
                    state.ready.status === "ready" ? "bg-emerald-500" : "bg-destructive"
                  }`}
                />
                {state.ready.status === "ready" ? "Database ready" : "Degraded"}
              </Badge>
            </div>
            <p className="text-sm text-muted-foreground">
              Database {state.ready.checks.database.status}
              {state.ready.checks.database.detail
                ? ` (${state.ready.checks.database.detail})`
                : ""}
              . Worker and checkpointer liveness are not observed by this probe. Ready HTTP{" "}
              {state.readyHttp}.
            </p>
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}
