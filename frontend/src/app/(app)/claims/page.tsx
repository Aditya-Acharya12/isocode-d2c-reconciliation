"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { ClaimPill, Money } from "@/components/reconciliation/ui";
import { NativeSelect } from "@/components/native-select";
import { EmptyState, ErrorState, PageHeader, RowsSkeleton } from "@/components/states";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { formatCount } from "@/lib/format";
import {
  CATEGORY_LABELS,
  CLAIM_LABELS,
  formatAmount,
  type Category,
  type ClaimStatus,
} from "@/lib/reconciliation";
import { useCan } from "@/lib/session";

interface Group {
  partner_id: number;
  partner: string;
  total: string;
  items: {
    discrepancy_id: number;
    order_id: number;
    external_order_id: string;
    category: Category;
    expected: string;
    actual: string;
    difference: string;
    due_date: string | null;
  }[];
}

interface ClaimRow {
  id: number;
  claim_no: string;
  partner_id: number;
  partner: string;
  order_id: number;
  external_order_id: string;
  category: Category;
  claimed: string;
  recovered: string;
  status: ClaimStatus;
  created_at: string;
}

function ReadyToClaim() {
  const canManage = useCan("manage_claims");
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [confirm, setConfirm] = useState<Group | null>(null);
  const groups = useQuery({ queryKey: ["claims-eligible"], queryFn: () => api<Group[]>("/claims/eligible") });

  const create = useMutation({
    mutationFn: (ids: number[]) =>
      api<ClaimRow[]>("/claims", { method: "POST", body: { discrepancy_ids: ids } }),
    onSuccess: (created) => {
      toast.success(`${created.length} ${created.length === 1 ? "claim" : "claims"} created`);
      setSelected(new Set());
      setConfirm(null);
      queryClient.invalidateQueries({ queryKey: ["claims-eligible"] });
      queryClient.invalidateQueries({ queryKey: ["claims"] });
    },
  });

  const toggle = (id: number) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  if (groups.isPending) return <RowsSkeleton rows={3} />;
  if (groups.isError) return <ErrorState error={groups.error} onRetry={() => groups.refetch()} />;
  if (groups.data.length === 0) {
    return (
      <EmptyState title="Nothing to claim">
        Open discrepancies appear here, grouped by partner, after reconciliation.
      </EmptyState>
    );
  }
  const chosen = (group: Group) => group.items.filter((item) => selected.has(item.discrepancy_id));

  return (
    <div className="space-y-4">
      {groups.data.map((group) => (
        <div key={group.partner_id} className="rounded-md border" data-testid={`eligible-${group.partner}`}>
          <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
            <div>
              <p className="text-sm font-semibold">{group.partner}</p>
              <p className="text-xs text-muted-foreground">
                {group.items.length} open {group.items.length === 1 ? "discrepancy" : "discrepancies"},{" "}
                {formatAmount(group.total)} in total
              </p>
            </div>
            {canManage && (
              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    setSelected((current) => {
                      const next = new Set(current);
                      const all = group.items.every((i) => next.has(i.discrepancy_id));
                      group.items.forEach((i) => (all ? next.delete(i.discrepancy_id) : next.add(i.discrepancy_id)));
                      return next;
                    })
                  }
                >
                  {group.items.every((i) => selected.has(i.discrepancy_id)) ? "Clear" : "Select all"}
                </Button>
                <Button size="sm" disabled={chosen(group).length === 0} onClick={() => setConfirm(group)}>
                  Create {chosen(group).length || ""} {chosen(group).length === 1 ? "claim" : "claims"}
                </Button>
              </div>
            )}
          </div>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  {canManage && <TableHead className="w-8" />}
                  <TableHead>Order</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead className="text-right">Expected</TableHead>
                  <TableHead className="text-right">Actual</TableHead>
                  <TableHead className="text-right">Difference</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {group.items.map((item) => (
                  <TableRow key={item.discrepancy_id}>
                    {canManage && (
                      <TableCell>
                        <input
                          type="checkbox"
                          aria-label={`Select ${item.external_order_id} ${CATEGORY_LABELS[item.category]}`}
                          checked={selected.has(item.discrepancy_id)}
                          onChange={() => toggle(item.discrepancy_id)}
                        />
                      </TableCell>
                    )}
                    <TableCell>
                      <Link href={`/reconciliation/${item.order_id}`} className="underline-offset-3 hover:underline">
                        {item.external_order_id}
                      </Link>
                    </TableCell>
                    <TableCell>{CATEGORY_LABELS[item.category]}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatAmount(item.expected)}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatAmount(item.actual)}</TableCell>
                    <TableCell className="text-right">
                      <Money value={formatAmount(item.difference)} strong />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      ))}
      {confirm && (
        <Dialog open onOpenChange={(open) => !open && setConfirm(null)}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Create {chosen(confirm).length} claims against {confirm.partner}?</DialogTitle>
              <DialogDescription>
                Each claim keeps a copy of today&apos;s calculation and the partner records behind it,
                so later reconciliation runs won&apos;t change what you send.
              </DialogDescription>
            </DialogHeader>
            {create.isError && (
              <p role="alert" className="text-sm text-destructive">
                {create.error.message}
              </p>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={() => setConfirm(null)}>
                Cancel
              </Button>
              <Button
                disabled={create.isPending}
                onClick={() => create.mutate(chosen(confirm).map((i) => i.discrepancy_id))}
              >
                Create claims
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}

function ClaimList() {
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const claims = useQuery({
    queryKey: ["claims", status, offset],
    queryFn: () =>
      api<{ items: ClaimRow[]; total: number; by_status: Record<string, { count: number; claimed: string; recovered: string }> }>(
        `/claims?limit=25&offset=${offset}${status ? `&status=${status}` : ""}`,
      ),
    placeholderData: keepPreviousData,
  });
  const partners = [...new Map((claims.data?.items ?? []).map((c) => [c.partner_id, c.partner])).entries()];

  return (
    <>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <Label htmlFor="claim-status" className="text-xs text-muted-foreground">
            Status
          </Label>
          <NativeSelect
            id="claim-status"
            className="w-48"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setOffset(0);
            }}
          >
            <option value="">All statuses</option>
            {Object.entries(CLAIM_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label} ({claims.data?.by_status[value]?.count ?? 0})
              </option>
            ))}
          </NativeSelect>
        </div>
        {partners.length > 0 && (
          <div className="flex flex-wrap gap-2 text-sm" aria-label="Evidence exports">
            {partners.map(([id, name]) => (
              <a
                key={id}
                href={`/api/v1/claims/evidence.csv?partner_id=${id}`}
                className="inline-flex h-8 items-center rounded-lg border px-2.5 font-medium hover:bg-muted"
                data-testid={`export-${name}`}
              >
                Evidence for {name} (CSV)
              </a>
            ))}
          </div>
        )}
      </div>
      {claims.isPending && <RowsSkeleton />}
      {claims.isError && <ErrorState error={claims.error} onRetry={() => claims.refetch()} />}
      {claims.isSuccess && claims.data.items.length === 0 && <EmptyState title="No claims yet" />}
      {claims.isSuccess && claims.data.items.length > 0 && (
        <>
          <div className="overflow-x-auto rounded-md border">
            <Table data-testid="claims-table">
              <TableHeader>
                <TableRow>
                  <TableHead>Claim</TableHead>
                  <TableHead>Partner</TableHead>
                  <TableHead>Order</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead className="text-right">Claimed</TableHead>
                  <TableHead className="text-right">Recovered</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {claims.data.items.map((claim) => (
                  <TableRow key={claim.id} data-testid={`claim-row-${claim.external_order_id}`}>
                    <TableCell>
                      <Link href={`/claims/${claim.id}`} className="font-medium underline-offset-3 hover:underline">
                        {claim.claim_no}
                      </Link>
                    </TableCell>
                    <TableCell>{claim.partner}</TableCell>
                    <TableCell>{claim.external_order_id}</TableCell>
                    <TableCell className="text-muted-foreground">{CATEGORY_LABELS[claim.category]}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatAmount(claim.claimed)}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatAmount(claim.recovered)}</TableCell>
                    <TableCell>
                      <ClaimPill status={claim.status} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
            <span className="tabular-nums">
              {formatCount(offset + 1)}–{formatCount(offset + claims.data.items.length)} of {formatCount(claims.data.total)}
            </span>
            <div className="space-x-2">
              <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>
                Previous
              </Button>
              <Button variant="outline" size="sm" disabled={offset + 25 >= claims.data.total} onClick={() => setOffset(offset + 25)}>
                Next
              </Button>
            </div>
          </div>
        </>
      )}
    </>
  );
}

export default function ClaimsPage() {
  const canManage = useCan("manage_claims");
  return (
    <>
      <PageHeader
        title="Claims"
        description={
          canManage
            ? "Claim open discrepancies from the partners who owe them, and track what's recovered."
            : "Claims raised by the finance team, and what's been recovered."
        }
      />
      <section aria-label="Ready to claim" className="mb-10">
        <h2 className="mb-3 text-sm font-semibold">Ready to claim</h2>
        <ReadyToClaim />
      </section>
      <section aria-label="Claims">
        <h2 className="mb-3 text-sm font-semibold">Claims</h2>
        <ClaimList />
      </section>
    </>
  );
}
