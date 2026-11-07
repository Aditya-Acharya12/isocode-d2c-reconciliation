"use client";

import { useQuery } from "@tanstack/react-query";

import { EmptyState, ErrorState, PageHeader, RowsSkeleton } from "@/components/states";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api } from "@/lib/api";

interface RateCard {
  id: number;
  name: string;
  version: number;
  status: string;
  effective_from: string;
  effective_to: string | null;
  settlement_rules: {
    channel: string;
    payment_method: string;
    event: string;
    payer: string;
    percent: number;
    fixed: number;
  }[];
  freight_rules: {
    courier: string;
    weight_slab: string;
    zone: string;
    shipment_type: string;
    base: number;
    cod_percent: number;
    cod_minimum: number;
  }[];
}

const rupee = (n: number) => `₹${n.toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;

export default function RateCardsPage() {
  const cards = useQuery({ queryKey: ["rate-cards"], queryFn: () => api<RateCard[]>("/rate-cards") });
  return (
    <>
      <PageHeader
        title="Rate cards"
        description="The commercial terms reconciliation uses. Each order uses the published card in force on its order date."
      />
      {cards.isPending && <RowsSkeleton />}
      {cards.isError && <ErrorState error={cards.error} onRetry={() => cards.refetch()} />}
      {cards.isSuccess && cards.data.length === 0 && (
        <EmptyState title="No rate cards">Without one, every order needs review.</EmptyState>
      )}
      {cards.data?.map((card) => (
        <section key={card.id} className="mb-10" aria-label={card.name}>
          <h2 className="text-sm font-semibold">
            {card.name}, version {card.version}{" "}
            <span className="font-normal text-muted-foreground">
              ({card.status}, from {card.effective_from}
              {card.effective_to ? ` to ${card.effective_to}` : ""})
            </span>
          </h2>
          <p className="mt-0.5 mb-3 text-xs text-muted-foreground">
            Demo values, invented except where the assignment&apos;s sample orders imply them. They
            must be confirmed against real partner contracts.
          </p>
          <h3 className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">Settlement</h3>
          <div className="mb-6 overflow-x-auto rounded-md border">
            <Table data-testid="settlement-rules">
              <TableHeader>
                <TableRow>
                  <TableHead>Channel</TableHead>
                  <TableHead>Payment</TableHead>
                  <TableHead>Event</TableHead>
                  <TableHead>Paid by</TableHead>
                  <TableHead className="text-right">Deduction</TableHead>
                  <TableHead className="text-right">Fixed</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {card.settlement_rules.map((rule, index) => (
                  <TableRow key={index}>
                    <TableCell>{rule.channel}</TableCell>
                    <TableCell>{rule.payment_method.toUpperCase()}</TableCell>
                    <TableCell>{rule.event.replace("_", " ")}</TableCell>
                    <TableCell>{rule.payer}</TableCell>
                    <TableCell className="text-right tabular-nums">{rule.percent}%</TableCell>
                    <TableCell className="text-right tabular-nums">{rupee(rule.fixed)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <h3 className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">Freight</h3>
          <div className="overflow-x-auto rounded-md border">
            <Table data-testid="freight-rules">
              <TableHeader>
                <TableRow>
                  <TableHead>Courier</TableHead>
                  <TableHead>Slab</TableHead>
                  <TableHead>Zone</TableHead>
                  <TableHead>Shipment</TableHead>
                  <TableHead className="text-right">Base</TableHead>
                  <TableHead className="text-right">COD charge</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {card.freight_rules.map((rule, index) => (
                  <TableRow key={index}>
                    <TableCell>{rule.courier}</TableCell>
                    <TableCell>{rule.weight_slab}</TableCell>
                    <TableCell>{rule.zone}</TableCell>
                    <TableCell>{rule.shipment_type}</TableCell>
                    <TableCell className="text-right tabular-nums">{rupee(rule.base)}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {rule.cod_percent ? `${rule.cod_percent}%, min ${rupee(rule.cod_minimum)}` : "–"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </section>
      ))}
    </>
  );
}
