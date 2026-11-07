import type { ReactNode } from "react";

import { CLAIM_LABELS, STATUS_LABELS, type ClaimStatus, type ReconStatus } from "@/lib/reconciliation";
import { cn } from "@/lib/utils";

const STATUS_STYLES: Record<ReconStatus, string> = {
  settled: "bg-emerald-100 text-emerald-900",
  pending: "bg-muted text-muted-foreground",
  needs_review: "bg-amber-100 text-amber-900",
  short_paid: "bg-destructive/10 text-destructive",
  unpaid_past_due: "bg-destructive/10 text-destructive",
  freight_overcharged: "bg-destructive/10 text-destructive",
  return_not_received: "bg-destructive/10 text-destructive",
};

export function StatusPill({ status }: { status: ReconStatus }) {
  return (
    <span
      data-testid="recon-status"
      className={cn(
        "inline-block rounded px-1.5 py-0.5 text-xs font-medium whitespace-nowrap",
        STATUS_STYLES[status],
      )}
    >
      {STATUS_LABELS[status]}
    </span>
  );
}

const CLAIM_STYLES: Record<ClaimStatus, string> = {
  draft: "bg-muted text-muted-foreground",
  submitted: "bg-sky-100 text-sky-900",
  acknowledged: "bg-sky-100 text-sky-900",
  partially_recovered: "bg-amber-100 text-amber-900",
  recovered: "bg-emerald-100 text-emerald-900",
  rejected: "bg-destructive/10 text-destructive",
  written_off: "bg-muted text-muted-foreground",
};

export function ClaimPill({ status }: { status: ClaimStatus }) {
  return (
    <span
      data-testid="claim-status"
      className={cn(
        "inline-block rounded px-1.5 py-0.5 text-xs font-medium whitespace-nowrap",
        CLAIM_STYLES[status],
      )}
    >
      {CLAIM_LABELS[status]}
    </span>
  );
}

/** A money figure. Negative amounts are what the brand is owed, so they stand out. */
export function Money({ value, strong = false }: { value: string; strong?: boolean }) {
  const negative = value.startsWith("-");
  return (
    <span
      className={cn(
        "tabular-nums whitespace-nowrap",
        negative && "text-destructive",
        strong && "font-medium",
      )}
    >
      {value}
    </span>
  );
}

export function MetricRow({ children }: { children: ReactNode }) {
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-4 rounded-md border p-4 sm:grid-cols-3 lg:grid-cols-6">
      {children}
    </dl>
  );
}

export function Metric({
  label,
  value,
  tone,
  testId,
  hint,
}: {
  label: string;
  value: string;
  tone?: "risk";
  testId?: string;
  hint?: string;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd
        data-testid={testId}
        className={cn(
          "mt-0.5 text-xl font-semibold tabular-nums",
          tone === "risk" && value !== "₹0.00" && "text-destructive",
        )}
      >
        {value}
      </dd>
      {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-dashed py-1.5 text-sm last:border-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right">{children}</dd>
    </div>
  );
}
