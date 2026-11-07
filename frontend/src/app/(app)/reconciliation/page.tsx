"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { Metric, MetricRow, Money, StatusPill } from "@/components/reconciliation/ui";
import { NativeSelect } from "@/components/native-select";
import { EmptyState, ErrorState, PageHeader, RowsSkeleton } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api } from "@/lib/api";
import { formatCount, formatDateTime } from "@/lib/format";
import {
  CATEGORY_LABELS,
  STATUS_LABELS,
  STATUS_ORDER,
  formatAmount,
  formatPaise,
  type ReconStatus,
  type ResultRow,
  type RunInfo,
  type Summary,
} from "@/lib/reconciliation";
import { useCan } from "@/lib/session";
import type { Partner } from "@/lib/types";

const PAGE_SIZE = 25;
const todayInIndia = () =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());

interface Filters {
  status: string;
  channel_id: string;
  partner_id: string;
  category: string;
  date_from: string;
  date_to: string;
  q: string;
  sort: string;
}
const EMPTY: Filters = {
  status: "",
  channel_id: "",
  partner_id: "",
  category: "",
  date_from: "",
  date_to: "",
  q: "",
  sort: "date",
};

function RunPanel({ summary }: { summary: Summary }) {
  const canOperate = useCan("operate_reconciliation");
  const queryClient = useQueryClient();
  const [asOf, setAsOf] = useState(todayInIndia());
  const last = summary.last_run;
  const busy = last?.status === "queued" || last?.status === "running";

  const start = useMutation({
    mutationFn: () =>
      api<RunInfo & { created: boolean }>("/reconciliation/runs", {
        method: "POST",
        body: { as_of_date: asOf },
      }),
    onSuccess: (run) => {
      toast.success(run.created ? "Reconciliation started" : "A run is already in progress");
      queryClient.invalidateQueries({ queryKey: ["recon-summary"] });
    },
  });

  return (
    <div className="flex flex-wrap items-end justify-between gap-4 rounded-md border p-4">
      <div className="text-sm" data-testid="last-run">
        {last ? (
          <>
            <p className="font-medium">
              {busy
                ? "Reconciling…"
                : last.status === "failed"
                  ? "The last run failed"
                  : `Last reconciled as of ${new Date(last.as_of_date).toLocaleDateString("en-IN", { dateStyle: "medium" })}`}
            </p>
            <p className="text-muted-foreground">
              {last.status === "failed"
                ? last.error
                : `Run ${last.id}, started ${formatDateTime(last.created_at)}.`}
            </p>
          </>
        ) : (
          <p className="text-muted-foreground">Nothing has been reconciled yet.</p>
        )}
      </div>
      {canOperate && (
        <div className="flex items-end gap-2">
          <div className="space-y-1">
            <Label htmlFor="as-of" className="text-xs text-muted-foreground">
              As of
            </Label>
            <Input
              id="as-of"
              type="date"
              value={asOf}
              max={todayInIndia()}
              onChange={(event) => setAsOf(event.target.value)}
              className="w-40"
            />
          </div>
          <Button disabled={busy || start.isPending || !asOf} onClick={() => start.mutate()}>
            {busy || start.isPending ? "Reconciling…" : "Run reconciliation"}
          </Button>
        </div>
      )}
      {start.isError && (
        <p role="alert" className="w-full text-sm text-destructive">
          {start.error.message}
        </p>
      )}
    </div>
  );
}

function Unmatched() {
  const unmatched = useQuery({
    queryKey: ["recon-unmatched"],
    queryFn: () =>
      api<
        {
          kind: string;
          id: number;
          partner: string;
          file_id: number;
          filename: string;
          row: number;
          order_ref: string | null;
          tracking_id: string | null;
          reason: string;
        }[]
      >("/reconciliation/unmatched"),
  });
  if (!unmatched.data?.length) return null;
  return (
    <section aria-label="Records not linked to an order" className="mt-10">
      <h2 className="text-sm font-semibold">Records not linked to an order</h2>
      <p className="mt-0.5 mb-3 text-sm text-muted-foreground">
        These partner records couldn&apos;t be matched to exactly one order, so they aren&apos;t in
        any total above. Nothing was guessed.
      </p>
      <div className="overflow-x-auto rounded-md border">
        <Table data-testid="unmatched-table">
          <TableHeader>
            <TableRow>
              <TableHead>Record</TableHead>
              <TableHead>Partner</TableHead>
              <TableHead>Order ID / AWB in file</TableHead>
              <TableHead>Why</TableHead>
              <TableHead>Source</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {unmatched.data.map((record) => (
              <TableRow key={`${record.kind}-${record.id}`}>
                <TableCell>{record.kind.replace("_", " ")}</TableCell>
                <TableCell>{record.partner}</TableCell>
                <TableCell className="font-mono text-xs">
                  {record.order_ref || record.tracking_id || "–"}
                </TableCell>
                <TableCell className="text-muted-foreground">{record.reason}</TableCell>
                <TableCell>
                  <Link href={`/imports/${record.file_id}`} className="underline underline-offset-3">
                    {record.filename}, row {record.row}
                  </Link>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

function TaxSummary() {
  const tax = useQuery({
    queryKey: ["recon-tax"],
    queryFn: () =>
      api<{
        note: string;
        partners: {
          partner: string;
          settlement_lines: number;
          tcs: string;
          tds: string;
          lines_without_tax_reported: number;
        }[];
        configured_rates: { channel: string; kind: string; rate_percent: number }[];
      }>("/reconciliation/tax"),
  });
  if (!tax.data) return null;
  return (
    <section aria-label="Tax deductions" className="mt-10">
      <h2 className="text-sm font-semibold">Tax withheld by partners</h2>
      <p className="mt-0.5 mb-3 max-w-3xl text-sm text-muted-foreground" data-testid="tax-note">
        {tax.data.note} TCS and TDS count as received, so they never appear as leakage.
      </p>
      {tax.data.partners.length === 0 ? (
        <EmptyState title="No settlement files imported yet" />
      ) : (
        <div className="overflow-x-auto rounded-md border">
          <Table data-testid="tax-table">
            <TableHeader>
              <TableRow>
                <TableHead>Partner</TableHead>
                <TableHead className="text-right">Settlement lines</TableHead>
                <TableHead className="text-right">GST TCS</TableHead>
                <TableHead className="text-right">Income tax TDS</TableHead>
                <TableHead className="text-right">Lines without tax reported</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {tax.data.partners.map((row) => (
                <TableRow key={row.partner}>
                  <TableCell>{row.partner}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCount(row.settlement_lines)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{formatAmount(row.tcs)}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatAmount(row.tds)}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCount(row.lines_without_tax_reported)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  );
}

export default function ReconciliationPage() {
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [offset, setOffset] = useState(0);
  const set = (patch: Partial<Filters>) => {
    setFilters((current) => ({ ...current, ...patch }));
    setOffset(0);
  };

  const summary = useQuery({
    queryKey: ["recon-summary"],
    queryFn: () => api<Summary>("/reconciliation/summary"),
    refetchInterval: (query) =>
      ["queued", "running"].includes(query.state.data?.last_run?.status ?? "") ? 2000 : false,
  });
  const runId = summary.data?.last_run?.status === "succeeded" ? summary.data.last_run.id : 0;
  const channels = useQuery({
    queryKey: ["sales-channels"],
    queryFn: () => api<{ id: number; name: string }[]>("/sales-channels"),
  });
  const partners = useQuery({ queryKey: ["partners"], queryFn: () => api<Partner[]>("/partners") });

  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
  const results = useQuery({
    queryKey: ["recon-results", params.toString(), runId],
    queryFn: () =>
      api<{ items: ResultRow[]; total: number }>(`/reconciliation/results?${params.toString()}`),
    placeholderData: keepPreviousData,
  });

  const data = summary.data;
  return (
    <>
      <PageHeader
        title="Reconciliation"
        description="Every order compared with what partners paid and charged, using the rate card in force on the order's date."
      />
      {summary.isPending && <RowsSkeleton />}
      {summary.isError && <ErrorState error={summary.error} onRetry={() => summary.refetch()} />}
      {data && (
        <div className="space-y-6">
          <RunPanel summary={data} />
          <MetricRow>
            <Metric label="Orders" value={formatCount(data.orders_total)} testId="metric-orders" />
            <Metric
              label="Reconciled"
              value={formatCount(data.reconciled)}
              hint={`of ${formatCount(data.orders_evaluated)} checked`}
              testId="metric-reconciled"
            />
            <Metric
              label="Open discrepancies"
              value={formatCount(data.open_discrepancies)}
              testId="metric-open"
            />
            <Metric
              label="Money at risk"
              value={formatAmount(data.money_at_risk)}
              tone="risk"
              hint="Unpaid, short-paid and overcharged"
              testId="metric-risk"
            />
            <Metric
              label="Stock at risk"
              value={formatAmount(data.inventory_at_risk)}
              tone="risk"
              hint="Returns not scanned in, at order value"
              testId="metric-inventory"
            />
            <Metric
              label="Recovered so far"
              value={formatAmount(data.recovered_on_open)}
              hint="On open discrepancies"
            />
          </MetricRow>

          <div className="flex flex-wrap gap-2" aria-label="Orders by status">
            {STATUS_ORDER.map((status) => (
              <button
                key={status}
                type="button"
                onClick={() => set({ status: filters.status === status ? "" : status })}
                aria-pressed={filters.status === status}
                className="flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs hover:bg-muted aria-pressed:border-foreground aria-pressed:bg-muted"
              >
                <StatusPill status={status} />
                <span className="tabular-nums">{formatCount(data.statuses[status] ?? 0)}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      <section aria-label="Orders" className="mt-8">
        <div className="mb-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="space-y-1">
            <Label htmlFor="q" className="text-xs text-muted-foreground">
              Search
            </Label>
            <Input
              id="q"
              placeholder="Order ID or AWB"
              value={filters.q}
              onChange={(event) => set({ q: event.target.value })}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="f-status" className="text-xs text-muted-foreground">
              Status
            </Label>
            <NativeSelect
              id="f-status"
              value={filters.status}
              onChange={(event) => set({ status: event.target.value })}
            >
              <option value="">All statuses</option>
              {STATUS_ORDER.map((status) => (
                <option key={status} value={status}>
                  {STATUS_LABELS[status]}
                </option>
              ))}
            </NativeSelect>
          </div>
          <div className="space-y-1">
            <Label htmlFor="f-category" className="text-xs text-muted-foreground">
              Open discrepancy
            </Label>
            <NativeSelect
              id="f-category"
              value={filters.category}
              onChange={(event) => set({ category: event.target.value })}
            >
              <option value="">Any</option>
              {Object.entries(CATEGORY_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </NativeSelect>
          </div>
          <div className="space-y-1">
            <Label htmlFor="f-channel" className="text-xs text-muted-foreground">
              Channel
            </Label>
            <NativeSelect
              id="f-channel"
              value={filters.channel_id}
              onChange={(event) => set({ channel_id: event.target.value })}
            >
              <option value="">All channels</option>
              {channels.data?.map((channel) => (
                <option key={channel.id} value={channel.id}>
                  {channel.name}
                </option>
              ))}
            </NativeSelect>
          </div>
          <div className="space-y-1">
            <Label htmlFor="f-partner" className="text-xs text-muted-foreground">
              Partner
            </Label>
            <NativeSelect
              id="f-partner"
              value={filters.partner_id}
              onChange={(event) => set({ partner_id: event.target.value })}
            >
              <option value="">All partners</option>
              {partners.data?.map((partner) => (
                <option key={partner.id} value={partner.id}>
                  {partner.name}
                </option>
              ))}
            </NativeSelect>
          </div>
          <div className="space-y-1">
            <Label htmlFor="f-from" className="text-xs text-muted-foreground">
              Ordered from
            </Label>
            <Input
              id="f-from"
              type="date"
              value={filters.date_from}
              onChange={(event) => set({ date_from: event.target.value })}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="f-to" className="text-xs text-muted-foreground">
              Ordered to
            </Label>
            <Input
              id="f-to"
              type="date"
              value={filters.date_to}
              onChange={(event) => set({ date_to: event.target.value })}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="f-sort" className="text-xs text-muted-foreground">
              Sort by
            </Label>
            <NativeSelect
              id="f-sort"
              value={filters.sort}
              onChange={(event) => set({ sort: event.target.value })}
            >
              <option value="date">Newest orders</option>
              <option value="difference">Largest difference</option>
            </NativeSelect>
          </div>
        </div>
        {JSON.stringify(filters) !== JSON.stringify(EMPTY) && (
          <Button variant="ghost" size="sm" className="mb-2" onClick={() => set(EMPTY)}>
            Clear filters
          </Button>
        )}

        {results.isPending && <RowsSkeleton />}
        {results.isError && <ErrorState error={results.error} onRetry={() => results.refetch()} />}
        {results.isSuccess && results.data.items.length === 0 && (
          <EmptyState
            title={
              data && data.orders_evaluated === 0
                ? "Nothing reconciled yet"
                : "No orders match these filters"
            }
          >
            {data && data.orders_evaluated === 0
              ? "Import orders and partner files, then run reconciliation."
              : undefined}
          </EmptyState>
        )}
        {results.isSuccess && results.data.items.length > 0 && (
          <>
            <div className="overflow-x-auto rounded-md border">
              <Table data-testid="recon-table">
                <TableHeader>
                  <TableRow>
                    <TableHead>Order</TableHead>
                    <TableHead>Channel</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Expected</TableHead>
                    <TableHead className="text-right">Received</TableHead>
                    <TableHead className="text-right">Difference</TableHead>
                    <TableHead className="min-w-64">Why</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {results.data.items.map((row) => (
                    <TableRow key={row.order_id} data-testid={`recon-row-${row.external_order_id}`}>
                      <TableCell className="whitespace-nowrap">
                        <Link
                          href={`/reconciliation/${row.order_id}`}
                          className="font-medium underline-offset-3 hover:underline"
                        >
                          {row.external_order_id}
                        </Link>
                        <div className="text-xs text-muted-foreground tabular-nums">
                          {new Date(row.order_date).toLocaleDateString("en-IN", {
                            dateStyle: "medium",
                          })}
                          , {formatAmount(row.order_value)}, {row.payment_method.toUpperCase()}
                        </div>
                      </TableCell>
                      <TableCell className="text-muted-foreground">{row.channel}</TableCell>
                      <TableCell>
                        <StatusPill status={row.status as ReconStatus} />
                      </TableCell>
                      <TableCell className="text-right">
                        <Money value={formatPaise(row.expected_paise)} />
                      </TableCell>
                      <TableCell className="text-right">
                        <Money value={formatPaise(row.actual_paise)} />
                      </TableCell>
                      <TableCell className="text-right" data-testid="difference">
                        <Money value={formatPaise(row.difference_paise)} strong />
                      </TableCell>
                      <TableCell className="max-w-80 text-xs whitespace-normal text-muted-foreground">
                        {row.reason}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
              <span className="tabular-nums">
                {formatCount(offset + 1)}–{formatCount(offset + results.data.items.length)} of{" "}
                {formatCount(results.data.total)}
              </span>
              <div className="space-x-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={offset + PAGE_SIZE >= results.data.total}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                >
                  Next
                </Button>
              </div>
            </div>
          </>
        )}
      </section>

      <Unmatched />
      <TaxSummary />
    </>
  );
}
