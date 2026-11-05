"use client";

import { useQuery } from "@tanstack/react-query";

import { EmptyState, ErrorState, PageHeader, RowsSkeleton } from "@/components/states";
import { api } from "@/lib/api";
import { formatCount } from "@/lib/format";
import type { WorkspaceSummary } from "@/lib/types";

const ROWS: { key: keyof WorkspaceSummary; label: string }[] = [
  { key: "partners", label: "Partners" },
  { key: "sales_channels", label: "Sales channels" },
  { key: "uploaded_files", label: "Files uploaded" },
  { key: "orders", label: "Orders" },
  { key: "reconciliation_runs", label: "Reconciliation runs" },
  { key: "open_claims", label: "Open claims" },
];

export default function OverviewPage() {
  const summary = useQuery({
    queryKey: ["workspace-summary"],
    queryFn: () => api<WorkspaceSummary>("/workspace/summary"),
  });

  return (
    <>
      <PageHeader title="Overview" description="What's currently in the workspace." />
      {summary.isPending && <RowsSkeleton />}
      {summary.isError && <ErrorState error={summary.error} onRetry={() => summary.refetch()} />}
      {summary.isSuccess && (
        <div className="space-y-8">
          {summary.data.orders === 0 && (
            <EmptyState title="No orders yet">
              Money-at-risk figures appear here once partner files are imported and reconciled.
            </EmptyState>
          )}
          <dl className="max-w-md divide-y border-y">
            {ROWS.map(({ key, label }) => (
              <div key={key} className="flex items-baseline justify-between py-2.5">
                <dt className="text-sm text-muted-foreground">{label}</dt>
                <dd className="text-sm font-medium tabular-nums" data-testid={`summary-${key}`}>
                  {formatCount(summary.data[key])}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </>
  );
}
