"use client";

import { useMutation } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import type { Session } from "@/lib/types";

type ServerState = "checking" | "waking" | "ready" | "unreachable";

const POLL_INTERVAL_MS = 3000;
const SHOW_WAKING_AFTER_MS = 2500;
const GIVE_UP_AFTER_MS = 180_000;

/**
 * Render's free tier sleeps when idle and takes about a minute to wake. Waiting here,
 * with an explanation, means a slow first request doesn't look like a broken product.
 */
function useServerState(): { state: ServerState; retry: () => void; demoMode: boolean } {
  const [state, setState] = useState<ServerState>("checking");
  const [demoMode, setDemoMode] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const startedAt = Date.now();
    const wakingTimer = setTimeout(() => {
      if (!cancelled) setState((current) => (current === "checking" ? "waking" : current));
    }, SHOW_WAKING_AFTER_MS);

    async function poll() {
      try {
        const response = await fetch("/api/healthz", { cache: "no-store" });
        if (response.ok) {
          const body = await response.json();
          if (!cancelled) {
            setDemoMode(body.demo_mode === true);
            setState("ready");
          }
          return;
        }
      } catch {
        // Treated the same as an unhealthy response below.
      }
      if (cancelled) return;
      if (Date.now() - startedAt > GIVE_UP_AFTER_MS) {
        setState("unreachable");
        return;
      }
      timer = setTimeout(poll, POLL_INTERVAL_MS);
    }
    poll();

    return () => {
      cancelled = true;
      clearTimeout(wakingTimer);
      if (timer) clearTimeout(timer);
    };
  }, [attempt]);

  return {
    state,
    demoMode,
    retry: () => {
      setState("checking");
      setAttempt((value) => value + 1);
    },
  };
}

/** Only allows paths on this site, so a crafted link can't redirect people elsewhere. */
function safeDestination(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") && !next.includes("\\")
    ? next
    : "/";
}

function LoginForm() {
  const searchParams = useSearchParams();
  const { state, retry, demoMode } = useServerState();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const login = useMutation({
    mutationFn: () =>
      // Repeating a login is harmless, and it may meet Neon waking from idle.
      api<Session>("/auth/login", {
        method: "POST",
        body: { email, password },
        retrySafe: true,
      }),
    onSuccess: () => window.location.assign(safeDestination(searchParams.get("next"))),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    login.mutate();
  }

  const busy = login.isPending || login.isSuccess;

  return (
    <main className="flex min-h-screen items-center justify-center bg-muted/40 px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6">
          <h1 className="text-xl font-semibold tracking-tight">Reconciliation</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Sign in to review orders, payments and claims.
          </p>
        </div>

        <form onSubmit={submit} className="space-y-4 rounded-lg border bg-background p-6">
          <div className="space-y-1.5">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>

          {login.isError && (
            <p role="alert" className="text-sm text-destructive">
              {login.error.message}
            </p>
          )}

          <Button type="submit" className="w-full" disabled={state !== "ready" || busy}>
            {busy ? "Signing in…" : state === "ready" ? "Sign in" : "Waiting for server…"}
          </Button>

          <div aria-live="polite" className="text-xs text-muted-foreground">
            {state === "waking" && (
              <p data-testid="server-waking">
                The server is starting after being idle. This can take about a minute.
              </p>
            )}
            {state === "unreachable" && (
              <p role="alert" className="text-destructive">
                The server isn&apos;t responding.{" "}
                <button type="button" className="underline" onClick={retry}>
                  Try again
                </button>
              </p>
            )}
          </div>
        </form>

        {demoMode && (
          <p className="mt-4 text-center text-xs text-muted-foreground">
            This demo contains synthetic data only.
          </p>
        )}
      </div>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
