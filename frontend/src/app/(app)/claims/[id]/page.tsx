"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

import { ClaimPill, Field, Money } from "@/components/reconciliation/ui";
import { NativeSelect } from "@/components/native-select";
import { ErrorState, RowsSkeleton } from "@/components/states";
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
import { formatDateTime } from "@/lib/format";
import {
  CATEGORY_LABELS,
  CLAIM_LABELS,
  formatAmount,
  formatPaise,
  type Category,
  type ClaimStatus,
} from "@/lib/reconciliation";
import { useCan } from "@/lib/session";

interface ClaimDetail {
  id: number;
  claim_no: string;
  partner: string;
  order_id: number;
  external_order_id: string;
  category: Category;
  expected: string;
  actual: string;
  difference: string;
  claimed: string;
  recovered: string;
  status: ClaimStatus;
  resolution_notes: string | null;
  created_at: string;
  calculation: string;
  discrepancy_state: string | null;
  allowed_transitions: ClaimStatus[];
  evidence: {
    order: Record<string, string | number | null>;
    records: {
      kind: string;
      reference: string | null;
      date: string | null;
      amount_paise: number | null;
      source_file: string | null;
      row: number;
      file_id: number;
    }[];
  };
  events: {
    from_status: ClaimStatus | null;
    to_status: ClaimStatus;
    amount: string | null;
    note: string | null;
    actor: string;
    at: string;
  }[];
}

function TransitionForm({ claim }: { claim: ClaimDetail }) {
  const queryClient = useQueryClient();
  const [to, setTo] = useState<ClaimStatus | "">("");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const needsAmount = to === "partially_recovered";
  const needsNote = to === "rejected" || to === "written_off";

  const save = useMutation({
    mutationFn: () =>
      api<ClaimDetail>(`/claims/${claim.id}/transitions`, {
        method: "POST",
        body: {
          to_status: to,
          // Recovered always means the full claimed amount, so it never sends one.
          recovered_amount: needsAmount && amount ? amount : null,
          note: note || null,
        },
      }),
    onSuccess: (updated) => {
      toast.success(`Claim marked ${CLAIM_LABELS[updated.status].toLowerCase()}`);
      setTo("");
      setAmount("");
      setNote("");
      queryClient.setQueryData(["claim", String(claim.id)], updated);
      queryClient.invalidateQueries({ queryKey: ["claims"] });
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    save.mutate();
  }

  if (claim.allowed_transitions.length === 0) {
    return <p className="text-sm text-muted-foreground">This claim is closed.</p>;
  }
  return (
    <form onSubmit={submit} className="space-y-3" aria-label="Update claim">
      <div className="space-y-1">
        <Label htmlFor="to-status">Move to</Label>
        <NativeSelect id="to-status" value={to} onChange={(event) => setTo(event.target.value as ClaimStatus)}>
          <option value="">Choose a status</option>
          {claim.allowed_transitions.map((status) => (
            <option key={status} value={status}>
              {CLAIM_LABELS[status]}
            </option>
          ))}
        </NativeSelect>
      </div>
      {to === "recovered" && (
        <p className="text-sm text-muted-foreground">
          Records the full {formatAmount(claim.claimed)} as recovered. If the partner paid less, use
          Partly recovered, then Written off for the rest.
        </p>
      )}
      {needsAmount && (
        <div className="space-y-1">
          <Label htmlFor="recovered">Total recovered so far</Label>
          <Input
            id="recovered"
            inputMode="decimal"
            placeholder={claim.recovered === "0.00" ? "0.00" : claim.recovered}
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
          />
        </div>
      )}
      <div className="space-y-1">
        <Label htmlFor="note">Note {needsNote ? "(required)" : "(optional)"}</Label>
        <Input id="note" value={note} onChange={(event) => setNote(event.target.value)} />
      </div>
      {save.isError && (
        <p role="alert" className="text-sm text-destructive">
          {save.error.message}
        </p>
      )}
      <Button type="submit" disabled={!to || save.isPending || (needsAmount && !amount) || (needsNote && !note.trim())}>
        {save.isPending ? "Saving…" : "Update claim"}
      </Button>
    </form>
  );
}

export default function ClaimPage() {
  const { id } = useParams<{ id: string }>();
  const canManage = useCan("manage_claims");
  const claim = useQuery({ queryKey: ["claim", id], queryFn: () => api<ClaimDetail>(`/claims/${id}`) });
  if (claim.isPending) return <RowsSkeleton />;
  if (claim.isError) return <ErrorState error={claim.error} onRetry={() => claim.refetch()} />;
  const data = claim.data;

  return (
    <>
      <nav className="mb-3 text-sm">
        <Link href="/claims" className="text-muted-foreground hover:text-foreground">
          ← Claims
        </Link>
      </nav>
      <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
            {data.claim_no} <ClaimPill status={data.status} />
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {CATEGORY_LABELS[data.category]} claim against {data.partner}, for order{" "}
            <Link href={`/reconciliation/${data.order_id}`} className="underline underline-offset-3">
              {data.external_order_id}
            </Link>
            .
          </p>
        </div>
        <a
          href={`/api/v1/claims/${data.id}/evidence.csv`}
          className="inline-flex h-8 items-center rounded-lg border px-2.5 text-sm font-medium hover:bg-muted"
          data-testid="claim-evidence"
        >
          Download evidence (CSV)
        </a>
      </header>
      {data.discrepancy_state === "resolved" && data.allowed_transitions.length > 0 && (
        <p className="mb-4 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900" data-testid="resolved-note">
          A later reconciliation found this discrepancy resolved, for example because the partner
          paid. Check before taking it further.
        </p>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <section className="rounded-md border p-4 lg:col-span-2" aria-label="Claim">
          <dl>
            <Field label="Expected">{formatAmount(data.expected)}</Field>
            <Field label="Actual">{formatAmount(data.actual)}</Field>
            <Field label="Difference">
              <Money value={formatAmount(data.difference)} strong />
            </Field>
            <Field label="Claimed">{formatAmount(data.claimed)}</Field>
            <Field label="Recovered">{formatAmount(data.recovered)}</Field>
          </dl>
          <h2 className="mt-4 mb-1 text-sm font-semibold">How it was calculated</h2>
          <p className="text-sm" data-testid="claim-calculation">
            {data.calculation}
          </p>
          {data.resolution_notes && (
            <p className="mt-3 text-sm">
              <span className="font-medium">Outcome:</span> {data.resolution_notes}
            </p>
          )}
        </section>
        <section className="rounded-md border p-4" aria-label="Status">
          <h2 className="mb-3 text-sm font-semibold">Status</h2>
          {canManage ? <TransitionForm claim={data} /> : <p className="text-sm text-muted-foreground">Only the finance team can update claims.</p>}
        </section>
      </div>

      <section className="mt-8" aria-label="Evidence">
        <h2 className="mb-1 text-sm font-semibold">Evidence, as filed</h2>
        <p className="mb-2 text-xs text-muted-foreground">Copied when the claim was created, so later runs can&apos;t change it.</p>
        <div className="overflow-x-auto rounded-md border">
          <Table data-testid="evidence-table">
            <TableHeader>
              <TableRow>
                <TableHead>Record</TableHead>
                <TableHead>Reference</TableHead>
                <TableHead>Date</TableHead>
                <TableHead className="text-right">Amount</TableHead>
                <TableHead>Source</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow>
                <TableCell>order</TableCell>
                <TableCell>{data.evidence.order.external_id}</TableCell>
                <TableCell>{data.evidence.order.order_date}</TableCell>
                <TableCell className="text-right tabular-nums">{formatAmount(String(data.evidence.order.value))}</TableCell>
                <TableCell>
                  {data.evidence.order.source_file}, row {data.evidence.order.source_row}
                </TableCell>
              </TableRow>
              {data.evidence.records.map((record, index) => (
                <TableRow key={index}>
                  <TableCell>{record.kind.replace("_", " ")}</TableCell>
                  <TableCell className="font-mono text-xs">{record.reference ?? "–"}</TableCell>
                  <TableCell>{record.date ?? "–"}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatPaise(record.amount_paise)}</TableCell>
                  <TableCell>
                    <Link href={`/imports/${record.file_id}`} className="underline underline-offset-3">
                      {record.source_file}, row {record.row}
                    </Link>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </section>

      <section className="mt-8" aria-label="History">
        <h2 className="mb-2 text-sm font-semibold">History</h2>
        <ol className="space-y-2 text-sm" data-testid="claim-history">
          {data.events.map((event, index) => (
            <li key={index} className="flex flex-wrap gap-x-2">
              <span className="text-muted-foreground tabular-nums">{formatDateTime(event.at)}</span>
              <span>
                {event.from_status ? `${CLAIM_LABELS[event.from_status]} → ` : "Created as "}
                {CLAIM_LABELS[event.to_status]}
                {event.amount && event.to_status !== "draft" ? `, ${formatAmount(event.amount)} recovered in total` : ""}
              </span>
              <span className="text-muted-foreground">by {event.actor}</span>
              {event.note && <span className="w-full text-muted-foreground">“{event.note}”</span>}
            </li>
          ))}
        </ol>
      </section>
    </>
  );
}
