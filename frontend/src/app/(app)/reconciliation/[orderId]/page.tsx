"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import type { ReactNode } from "react";

import { ClaimPill, Field, Money, StatusPill } from "@/components/reconciliation/ui";
import { ErrorState, RowsSkeleton } from "@/components/states";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api } from "@/lib/api";
import {
  CATEGORY_LABELS,
  STATUS_LABELS,
  formatAmount,
  formatPaise,
  percent,
  type Category,
  type ClaimStatus,
  type ReconStatus,
} from "@/lib/reconciliation";

// The explanation is the engine's own record, so its shape is read loosely.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Stream = Record<string, any>;

interface Detail {
  order: {
    id: number;
    external_order_id: string;
    channel: string;
    order_date: string;
    order_value: string;
    payment_method: string;
    delivery_status: string;
    tracking_id: string | null;
    courier: string | null;
    delivered_at: string | null;
    returned_at: string | null;
    weight_slab: string | null;
    zone: string | null;
    file_id: number;
    filename: string | null;
    row: number;
  };
  result: {
    status: ReconStatus;
    expected_paise: number | null;
    actual_paise: number | null;
    explanation: { reason: string; primary_stream: string; streams: Record<string, Stream> };
    run_id: number;
    as_of_date: string;
  } | null;
  discrepancies: {
    id: number;
    category: Category;
    partner: string;
    expected: string;
    actual: string;
    difference: string;
    due_date: string | null;
    state: string;
    claim: { id: number; claim_no: string; status: ClaimStatus } | null;
  }[];
  records: {
    kind: string;
    id: number;
    partner: string;
    file_id: number;
    filename: string;
    row: number;
    match_method: string | null;
    reference: string | null;
    date: string | null;
    amount: string | null;
    tracking_id: string | null;
  }[];
  partners: Record<string, string>;
}

const STREAM_LABELS: Record<string, string> = {
  receivable: "Payment",
  freight: "Freight",
  returns: "Return",
};

const date = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleDateString("en-IN", { dateStyle: "medium" }) : "–";

function Card({ title, status, children }: { title: string; status?: string; children: ReactNode }) {
  return (
    <section className="rounded-md border p-4" aria-label={title}>
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{title}</h3>
        {status && <StatusPill status={status as ReconStatus} />}
      </div>
      {children}
    </section>
  );
}

function Receivable({ stream, partners }: { stream: Stream; partners: Record<string, string> }) {
  if (!stream.rule) {
    return <p className="text-sm text-muted-foreground">{stream.reason}</p>;
  }
  const payer = partners[String(stream.payer_partner_id)] ?? "the courier";
  return (
    <>
      <dl data-testid="receivable-calculation">
        <Field label="Order value">{formatPaise(stream.base_paise)}</Field>
        {stream.event === "delivered" ? (
          <Field label={`Less ${percent(stream.rule.pct_bps)}${stream.rule.fixed_paise ? ` and ${formatPaise(stream.rule.fixed_paise)}` : ""}`}>
            <Money value={formatPaise(-stream.deduction_paise)} />
          </Field>
        ) : (
          <Field label="Return charge expected">
            <Money value={formatPaise(stream.expected_paise)} />
          </Field>
        )}
        <Field label="Expected, rounded to the rupee">
          <span className="font-medium tabular-nums">{formatPaise(stream.expected_paise)}</span>
        </Field>
        <Field label={`Received from ${payer}`}>{formatPaise(stream.net_received_paise)}</Field>
        <Field label="GST TCS withheld">{formatPaise(stream.tcs_paise)}</Field>
        <Field label="Income tax TDS withheld">{formatPaise(stream.tds_paise)}</Field>
        <Field label="Counted as received">
          <span className="font-medium tabular-nums">{formatPaise(stream.actual_paise)}</span>
        </Field>
        <Field label="Difference">
          <Money value={formatPaise(stream.difference_paise)} strong />
        </Field>
        <Field label="Due">{date(stream.due_date)}</Field>
      </dl>
      <p className="mt-2 text-xs text-muted-foreground">
        {stream.rule.rate_card}, via {stream.paid_via === "cod_remittance" ? "COD remittance" : "settlement"}.
        Differences of {formatPaise(stream.threshold_paise)} or less are tolerated.
      </p>
    </>
  );
}

function Freight({ stream }: { stream: Stream }) {
  return (
    <div className="overflow-x-auto">
      <Table data-testid="freight-calculation">
        <TableHeader>
          <TableRow>
            <TableHead>Invoice</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Slab</TableHead>
            <TableHead className="text-right">Expected</TableHead>
            <TableHead className="text-right">Billed excl. GST</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {(stream.lines ?? []).map((line: Stream) => (
            <TableRow key={line.id}>
              <TableCell>{line.reference}</TableCell>
              <TableCell>{line.shipment_type}</TableCell>
              <TableCell>{line.slab_used ?? "–"}</TableCell>
              <TableCell className="text-right tabular-nums">{formatPaise(line.expected_paise)}</TableCell>
              <TableCell className="text-right tabular-nums">
                {formatPaise(line.billed_excl_gst_paise)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function Returns({ stream }: { stream: Stream }) {
  return (
    <dl>
      <Field label="Returned on">{date(stream.returned_at)}</Field>
      <Field label="Grace period">{stream.grace_days !== undefined ? `${stream.grace_days} days` : "–"}</Field>
      <Field label="Scan due by">{date(stream.due_date)}</Field>
      <Field label="Warehouse scan">
        {stream.receipts?.length ? `Yes, ${stream.receipts[0].reference} on ${date(stream.receipts[0].date)}` : "None"}
      </Field>
    </dl>
  );
}

export default function OrderReconciliationPage() {
  const { orderId } = useParams<{ orderId: string }>();
  const detail = useQuery({
    queryKey: ["recon-order", orderId],
    queryFn: () => api<Detail>(`/reconciliation/orders/${orderId}`),
  });
  if (detail.isPending) return <RowsSkeleton />;
  if (detail.isError) return <ErrorState error={detail.error} onRetry={() => detail.refetch()} />;
  const { order, result, discrepancies, records, partners } = detail.data;
  const streams = result?.explanation.streams ?? {};

  return (
    <>
      <nav className="mb-3 text-sm">
        <Link href="/reconciliation" className="text-muted-foreground hover:text-foreground">
          ← Reconciliation
        </Link>
      </nav>
      <header className="mb-6">
        <h1 className="flex flex-wrap items-center gap-2 text-xl font-semibold tracking-tight">
          {order.external_order_id}
          {result && <StatusPill status={result.status} />}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {order.channel}, ordered {date(order.order_date)}, {formatAmount(order.order_value)}{" "}
          {order.payment_method.toUpperCase()}, {order.delivery_status.replace("_", " ")}.
        </p>
        {result && (
          <p className="mt-2 max-w-3xl text-sm" data-testid="order-reason">
            {STREAM_LABELS[result.explanation.primary_stream] && (
              <span className="font-medium">{STREAM_LABELS[result.explanation.primary_stream]}: </span>
            )}
            {result.explanation.reason}
          </p>
        )}
      </header>

      {!result ? (
        <p className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
          This order hasn&apos;t been reconciled yet. Run reconciliation to check it.
        </p>
      ) : (
        <>
          <p className="mb-3 text-xs text-muted-foreground">
            Checked as of {date(result.as_of_date)} in run {result.run_id}. Each part is owed by a
            different party, so each is checked separately, and the most serious status is shown
            above.
          </p>
          <div className="grid gap-4 lg:grid-cols-2">
            {streams.receivable && (
              <Card title="Payment for the order" status={streams.receivable.status}>
                <Receivable stream={streams.receivable} partners={partners} />
                <p className="mt-2 text-sm">{streams.receivable.reason}</p>
              </Card>
            )}
            {streams.freight && (
              <Card title="Freight charges" status={streams.freight.status}>
                <Freight stream={streams.freight} />
                <p className="mt-2 text-sm">{streams.freight.reason}</p>
              </Card>
            )}
            {streams.returns && (
              <Card title="Return to warehouse" status={streams.returns.status}>
                <Returns stream={streams.returns} />
                <p className="mt-2 text-sm">{streams.returns.reason}</p>
              </Card>
            )}
            <Card title="Order details">
              <dl>
                <Field label="Tracking ID">{order.tracking_id ?? "–"}</Field>
                <Field label="Courier">{order.courier ?? "–"}</Field>
                <Field label="Delivered">{date(order.delivered_at)}</Field>
                <Field label="Weight slab / zone">
                  {order.weight_slab ?? "–"} / {order.zone ?? "–"}
                </Field>
                <Field label="From">
                  <Link href={`/imports/${order.file_id}`} className="underline underline-offset-3">
                    {order.filename}, row {order.row}
                  </Link>
                </Field>
              </dl>
            </Card>
          </div>
        </>
      )}

      <section className="mt-8" aria-label="Discrepancies">
        <h2 className="mb-2 text-sm font-semibold">Discrepancies</h2>
        {discrepancies.length === 0 ? (
          <p className="text-sm text-muted-foreground">None.</p>
        ) : (
          <div className="overflow-x-auto rounded-md border">
            <Table data-testid="discrepancies-table">
              <TableHeader>
                <TableRow>
                  <TableHead>Type</TableHead>
                  <TableHead>Owed by</TableHead>
                  <TableHead className="text-right">Expected</TableHead>
                  <TableHead className="text-right">Actual</TableHead>
                  <TableHead className="text-right">Difference</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead>Claim</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {discrepancies.map((d) => (
                  <TableRow key={d.id}>
                    <TableCell>{CATEGORY_LABELS[d.category] ?? d.category}</TableCell>
                    <TableCell>{d.partner}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatAmount(d.expected)}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatAmount(d.actual)}</TableCell>
                    <TableCell className="text-right">
                      <Money value={formatAmount(d.difference)} strong />
                    </TableCell>
                    <TableCell className="text-muted-foreground">{d.state}</TableCell>
                    <TableCell>
                      {d.claim ? (
                        <Link href={`/claims/${d.claim.id}`} className="flex items-center gap-1.5">
                          <span className="underline underline-offset-3">{d.claim.claim_no}</span>
                          <ClaimPill status={d.claim.status} />
                        </Link>
                      ) : d.state === "open" ? (
                        <Link href="/claims" className="text-muted-foreground underline underline-offset-3">
                          Not claimed
                        </Link>
                      ) : (
                        "–"
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </section>

      <section className="mt-8" aria-label="Source records">
        <h2 className="mb-2 text-sm font-semibold">Partner records for this order</h2>
        {records.length === 0 ? (
          <p className="text-sm text-muted-foreground">No partner records are linked to this order.</p>
        ) : (
          <div className="overflow-x-auto rounded-md border">
            <Table data-testid="source-records">
              <TableHeader>
                <TableRow>
                  <TableHead>Record</TableHead>
                  <TableHead>Partner</TableHead>
                  <TableHead>Reference</TableHead>
                  <TableHead>Date</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead>Matched by</TableHead>
                  <TableHead>Source</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {records.map((record) => (
                  <TableRow key={`${record.kind}-${record.id}`}>
                    <TableCell>{record.kind.replace("_", " ")}</TableCell>
                    <TableCell>{record.partner}</TableCell>
                    <TableCell className="font-mono text-xs">{record.reference ?? "–"}</TableCell>
                    <TableCell className="tabular-nums">{date(record.date)}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatAmount(record.amount)}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {record.match_method?.replace(/_/g, " ") ?? "–"}
                    </TableCell>
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
        )}
      </section>
      {result && (
        <p className="mt-6 text-xs text-muted-foreground">
          Status precedence: {Object.values(STATUS_LABELS).join(" › ")}. Figures come from the rate
          card and the partner records above, never from AI.
        </p>
      )}
    </>
  );
}
