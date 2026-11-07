"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { StatusPill } from "@/components/reconciliation/ui";
import { EmptyState, ErrorState, PageHeader, RowsSkeleton } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { formatAmount, type ReconStatus } from "@/lib/reconciliation";
import { cn } from "@/lib/utils";

interface ReturnRow {
  order_id: number;
  external_order_id: string;
  channel: string;
  return_status: string;
  return_partner: string | null;
  returned_at: string | null;
  days_since_return: number | null;
  grace_days: number | null;
  due_date: string | null;
  received: boolean;
  received_at: string | null;
  receipt_reference: string | null;
  outcome: ReconStatus;
  reason: string;
  value_at_risk: string;
  evidence_files: number[];
}

const TABS = [
  ["all", "All returns", null],
  ["awaiting", "Awaiting scan", "pending"],
  ["not_received", "Not received", "return_not_received"],
  ["received", "Received", "settled"],
  ["review", "Needs review", "needs_review"],
] as const;
const PAGE_SIZE = 25;
const date = (value: string | null) =>
  value ? new Date(value).toLocaleDateString("en-IN", { dateStyle: "medium" }) : "–";

export default function ReturnsPage() {
  const [state, setState] = useState<(typeof TABS)[number][0]>("all");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const returns = useQuery({
    queryKey: ["returns", state, q, offset],
    queryFn: () =>
      api<{ items: ReturnRow[]; total: number; counts: Record<string, number> }>(
        `/reconciliation/returns?state=${state}&limit=${PAGE_SIZE}&offset=${offset}${q ? `&q=${encodeURIComponent(q)}` : ""}`,
      ),
    placeholderData: keepPreviousData,
  });
  const counts = returns.data?.counts ?? {};
  const all = Object.values(counts).reduce((sum, n) => sum + n, 0);

  return (
    <>
      <PageHeader
        title="Returns"
        description="A return counts as received only when the warehouse has scanned it in. A partner marking it returned isn't enough."
      />
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-1" role="tablist" aria-label="Return state">
          {TABS.map(([value, label, status]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={state === value}
              onClick={() => {
                setState(value);
                setOffset(0);
              }}
              className={cn(
                "rounded-md px-2.5 py-1 text-sm",
                state === value ? "bg-foreground/[0.06] font-medium" : "text-muted-foreground hover:bg-muted",
              )}
            >
              {label}{" "}
              <span className="tabular-nums">({formatCount(status ? counts[status] ?? 0 : all)})</span>
            </button>
          ))}
        </div>
        <Input
          aria-label="Search returns"
          placeholder="Order ID or AWB"
          className="w-56"
          value={q}
          onChange={(event) => {
            setQ(event.target.value);
            setOffset(0);
          }}
        />
      </div>
      {returns.isPending && <RowsSkeleton />}
      {returns.isError && <ErrorState error={returns.error} onRetry={() => returns.refetch()} />}
      {returns.isSuccess && returns.data.items.length === 0 && (
        <EmptyState title="No returns here">
          Returns appear after orders and return files are imported and reconciled.
        </EmptyState>
      )}
      {returns.isSuccess && returns.data.items.length > 0 && (
        <>
          <div className="overflow-x-auto rounded-md border">
            <Table data-testid="returns-table">
              <TableHeader>
                <TableRow>
                  <TableHead>Order</TableHead>
                  <TableHead>Return</TableHead>
                  <TableHead className="text-right">Days since</TableHead>
                  <TableHead>Warehouse scan</TableHead>
                  <TableHead>Outcome</TableHead>
                  <TableHead className="text-right">Stock at risk</TableHead>
                  <TableHead>Evidence</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {returns.data.items.map((row) => (
                  <TableRow key={row.order_id} data-testid={`return-row-${row.external_order_id}`}>
                    <TableCell className="whitespace-nowrap">
                      <Link
                        href={`/reconciliation/${row.order_id}`}
                        className="font-medium underline-offset-3 hover:underline"
                      >
                        {row.external_order_id}
                      </Link>
                      <div className="text-xs text-muted-foreground">{row.channel}</div>
                    </TableCell>
                    <TableCell>
                      <div>{row.return_status.replace(/_/g, " ")}</div>
                      <div className="text-xs text-muted-foreground">
                        {date(row.returned_at)}
                        {row.return_partner ? `, ${row.return_partner}` : ""}
                      </div>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {row.days_since_return ?? "–"}
                      {row.grace_days !== null && (
                        <div className="text-xs text-muted-foreground">grace {row.grace_days}</div>
                      )}
                    </TableCell>
                    <TableCell data-testid="scan">
                      {row.received ? (
                        <>
                          <div>Yes, {row.receipt_reference}</div>
                          <div className="text-xs text-muted-foreground">{date(row.received_at)}</div>
                        </>
                      ) : (
                        <span className="font-medium text-destructive">Not scanned</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <StatusPill status={row.outcome} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {row.value_at_risk === "0" ? "–" : formatAmount(row.value_at_risk)}
                    </TableCell>
                    <TableCell className="space-x-2 text-xs">
                      {row.evidence_files.length === 0
                        ? "Order file only"
                        : row.evidence_files.map((id) => (
                            <Link key={id} href={`/imports/${id}`} className="underline underline-offset-3">
                              File {id}
                            </Link>
                          ))}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
            <span className="tabular-nums">
              {formatCount(offset + 1)}–{formatCount(offset + returns.data.items.length)} of{" "}
              {formatCount(returns.data.total)}
            </span>
            <div className="space-x-2">
              <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={offset + PAGE_SIZE >= returns.data.total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next
              </Button>
            </div>
          </div>
        </>
      )}
    </>
  );
}
