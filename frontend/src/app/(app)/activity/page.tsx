"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { EmptyState, ErrorState, PageHeader, RowsSkeleton } from "@/components/states";
import { Button } from "@/components/ui/button";
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
import { useCan } from "@/lib/session";
import type { AuditEntry, Page } from "@/lib/types";

const PAGE_SIZE = 25;

const ACTION_LABELS: Record<string, string> = {
  "auth.login": "Signed in",
  "auth.login_failed": "Failed sign-in",
  "auth.logout": "Signed out",
  "partner.create": "Added partner",
  "partner.update": "Changed partner",
};

export default function ActivityPage() {
  const seesEveryone = useCan("view_all_audit");
  const [offset, setOffset] = useState(0);

  const logs = useQuery({
    queryKey: ["audit-logs", offset],
    queryFn: () => api<Page<AuditEntry>>(`/audit-logs?limit=${PAGE_SIZE}&offset=${offset}`),
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <PageHeader
        title={seesEveryone ? "Audit log" : "My activity"}
        description={
          seesEveryone
            ? "Every recorded action by every user. Entries can't be changed or deleted."
            : "Actions recorded under your account. Entries can't be changed or deleted."
        }
      />

      {logs.isPending && <RowsSkeleton />}
      {logs.isError && <ErrorState error={logs.error} onRetry={() => logs.refetch()} />}
      {logs.isSuccess && logs.data.items.length === 0 && (
        <EmptyState title="Nothing recorded yet" />
      )}
      {logs.isSuccess && logs.data.items.length > 0 && (
        <>
          <div className="overflow-x-auto rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>When</TableHead>
                  <TableHead>Who</TableHead>
                  <TableHead>Action</TableHead>
                  <TableHead>Record</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {logs.data.items.map((entry) => (
                  <TableRow key={entry.id}>
                    <TableCell className="text-muted-foreground tabular-nums">
                      {formatDateTime(entry.created_at)}
                    </TableCell>
                    <TableCell>{entry.actor_email ?? "Unknown account"}</TableCell>
                    <TableCell>{ACTION_LABELS[entry.action] ?? entry.action}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {entry.entity_type}
                      {entry.entity_id ? ` #${entry.entity_id}` : ""}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
            <span className="tabular-nums">
              {formatCount(offset + 1)}–{formatCount(offset + logs.data.items.length)} of{" "}
              {formatCount(logs.data.total)}
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
                disabled={offset + PAGE_SIZE >= logs.data.total}
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
