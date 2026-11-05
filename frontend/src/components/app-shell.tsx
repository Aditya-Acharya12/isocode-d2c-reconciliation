"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { toast } from "sonner";

import { ErrorState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { ROLE_LABELS, useSession } from "@/lib/session";
import type { Permission } from "@/lib/types";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  permission: Permission;
}

// The API enforces these permissions too. Hiding a link here is only a convenience.
const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Overview", permission: "view_workspace" },
  { href: "/settings/partners", label: "Partners", permission: "view_config" },
  { href: "/activity", label: "Audit log", permission: "view_all_audit" },
  { href: "/activity", label: "My activity", permission: "view_own_audit" },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const queryClient = useQueryClient();
  const session = useSession();

  const signOut = useMutation({
    mutationFn: () => api<void>("/auth/logout", { method: "POST" }),
    onSuccess: () => {
      queryClient.clear();
      router.replace("/login");
    },
    onError: (error) => toast.error(error.message),
  });

  if (session.isPending) {
    return (
      <div className="mx-auto max-w-5xl space-y-4 p-8" role="status" aria-label="Loading">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (session.isError) {
    return (
      <div className="mx-auto max-w-xl p-8">
        <ErrorState error={session.error} onRetry={() => session.refetch()} />
      </div>
    );
  }

  const { user, permissions, demo_mode: demoMode } = session.data;
  const items = NAV_ITEMS.filter((item) => permissions.includes(item.permission));

  return (
    <div className="flex min-h-screen flex-col">
      {demoMode && (
        <div className="border-b bg-amber-50 px-4 py-1.5 text-center text-xs text-amber-900">
          Synthetic demo data. These aren&apos;t real orders, partners or payments.
        </div>
      )}
      <div className="flex flex-1 flex-col md:flex-row">
        <aside className="flex shrink-0 flex-col border-b bg-sidebar md:w-56 md:border-r md:border-b-0">
          <div className="px-5 pt-5 pb-3 md:pb-6">
            <p className="text-sm font-semibold tracking-tight">Reconciliation</p>
            <p className="text-xs text-muted-foreground">Loom &amp; Fold (demo)</p>
          </div>
          <nav aria-label="Main" className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-col md:pb-0">
            {items.map((item) => {
              const active = pathname === item.href;
              return (
                <Link
                  key={item.label}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "rounded-md px-2.5 py-1.5 text-sm whitespace-nowrap transition-colors",
                    active
                      ? "bg-foreground/[0.06] font-medium"
                      : "text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground",
                  )}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
          <div className="flex items-center justify-between gap-3 border-t px-5 py-3 md:mt-auto md:block md:space-y-2 md:py-4">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium" data-testid="current-user">
                {user.full_name}
              </p>
              <p className="text-xs text-muted-foreground" data-testid="current-role">
                {ROLE_LABELS[user.role]}
              </p>
            </div>
            <Button
              variant="outline"
              size="sm"
              disabled={signOut.isPending}
              onClick={() => signOut.mutate()}
            >
              {signOut.isPending ? "Signing out…" : "Sign out"}
            </Button>
          </div>
        </aside>
        <main className="min-w-0 flex-1 px-4 py-6 md:px-10 md:py-8">
          <div className="mx-auto max-w-5xl">{children}</div>
        </main>
      </div>
    </div>
  );
}
