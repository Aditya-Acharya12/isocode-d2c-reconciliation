"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { IssueGroups, SeverityPill, Stat, StatRow } from "@/components/imports/status";
import { NativeSelect } from "@/components/native-select";
import { EmptyState, ErrorState, RowsSkeleton } from "@/components/states";
import { Button } from "@/components/ui/button";
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
import type {
  FileDetail,
  FileTypeSchema,
  ImportDetail,
  RowIssue,
  Severity,
} from "@/lib/ingestion";
import { useCan } from "@/lib/session";

const PAGE_SIZE = 25;

function RowIssues({
  importId,
  fileSchema,
  total,
}: {
  importId: number;
  fileSchema?: FileTypeSchema;
  total: number;
}) {
  const [severity, setSeverity] = useState<Severity | "">("");
  const [offset, setOffset] = useState(0);
  const issues = useQuery({
    queryKey: ["import-errors", importId, severity, offset],
    queryFn: () =>
      api<{ items: RowIssue[]; total: number }>(
        `/imports/${importId}/errors?limit=${PAGE_SIZE}&offset=${offset}${severity ? `&severity=${severity}` : ""}`,
      ),
    placeholderData: keepPreviousData,
  });

  return (
    <section aria-label="Row issues">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <h2 className="text-sm font-semibold">Rows to look at</h2>
        <div className="flex flex-wrap items-center gap-2">
          <Label htmlFor="severity-filter" className="text-xs text-muted-foreground">
            Show
          </Label>
          <NativeSelect
            id="severity-filter"
            className="w-36"
            value={severity}
            onChange={(event) => {
              setSeverity(event.target.value as Severity | "");
              setOffset(0);
            }}
          >
            <option value="">Everything</option>
            <option value="error">Errors</option>
            <option value="duplicate">Duplicates</option>
            <option value="warning">Warnings</option>
          </NativeSelect>
          {total > 0 && (
            <a
              href={`/api/v1/imports/${importId}/errors.csv`}
              className="inline-flex h-8 items-center rounded-lg border px-2.5 text-sm font-medium hover:bg-muted"
              data-testid="download-issues"
            >
              Download CSV
            </a>
          )}
        </div>
      </div>
      {issues.isPending && <RowsSkeleton rows={4} />}
      {issues.isError && <ErrorState error={issues.error} onRetry={() => issues.refetch()} />}
      {issues.isSuccess && issues.data.items.length === 0 && (
        <EmptyState title={severity ? "Nothing of this kind" : "Every row imported cleanly"} />
      )}
      {issues.isSuccess && issues.data.items.length > 0 && (
        <>
          <div className="overflow-x-auto rounded-md border">
            <Table data-testid="issues-table">
              <TableHeader>
                <TableRow>
                  <TableHead>Row</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Field</TableHead>
                  <TableHead>Value in file</TableHead>
                  <TableHead className="min-w-72">What&apos;s wrong</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {issues.data.items.map((issue, index) => (
                  <TableRow key={`${issue.row_number}-${issue.code}-${index}`}>
                    <TableCell className="tabular-nums">{issue.row_number}</TableCell>
                    <TableCell>
                      <SeverityPill severity={issue.severity} />
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {fileSchema?.fields.find((f) => f.name === issue.field)?.label ?? "–"}
                    </TableCell>
                    <TableCell className="max-w-44 truncate font-mono text-xs" title={issue.raw_value ?? ""}>
                      {issue.raw_value || "–"}
                    </TableCell>
                    <TableCell className="text-sm whitespace-normal">{issue.message}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
            <span className="tabular-nums">
              {formatCount(offset + 1)}–{formatCount(offset + issues.data.items.length)} of{" "}
              {formatCount(issues.data.total)}
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
                disabled={offset + PAGE_SIZE >= issues.data.total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next
              </Button>
            </div>
          </div>
        </>
      )}
    </section>
  );
}

export function ImportResult({
  file,
  fileSchema,
}: {
  file: FileDetail;
  fileSchema?: FileTypeSchema;
}) {
  const queryClient = useQueryClient();
  const canOperate = useCan("operate_reconciliation");
  const importId = file.import_job!.id;
  const detail = useQuery({
    queryKey: ["import", importId, file.stage],
    queryFn: () => api<ImportDetail>(`/imports/${importId}`),
  });

  const retry = useMutation({
    mutationFn: () => api<ImportDetail>(`/imports/${importId}/retry`, { method: "POST" }),
    onSuccess: () => {
      toast.success("Import queued again");
      queryClient.invalidateQueries({ queryKey: ["file", String(file.id)] });
      queryClient.invalidateQueries({ queryKey: ["files"] });
    },
  });

  if (detail.isPending) return <RowsSkeleton />;
  if (detail.isError) return <ErrorState error={detail.error} onRetry={() => detail.refetch()} />;
  const result = detail.data;

  if (file.stage === "import_failed") {
    return (
      <div role="alert" className="rounded-md border border-destructive/30 p-4">
        <h2 className="text-sm font-semibold text-destructive">This import didn&apos;t complete</h2>
        <p className="mt-1 text-sm" data-testid="import-error">
          {file.error}
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          Nothing was imported, so it&apos;s safe to try again.
        </p>
        {canOperate && (
          <Button className="mt-3" disabled={retry.isPending} onClick={() => retry.mutate()}>
            {retry.isPending ? "Retrying…" : "Retry import"}
          </Button>
        )}
        {retry.isError && (
          <p role="alert" className="mt-2 text-sm text-destructive">
            {retry.error.message}
          </p>
        )}
      </div>
    );
  }

  const groups = result.summary?.issue_groups ?? [];
  return (
    <div className="space-y-8">
      <section aria-label="Import summary">
        <StatRow>
          <Stat label="Rows in file" value={result.rows_total ?? 0} testId="result-total" />
          <Stat label="New records" value={result.rows_inserted ?? 0} testId="result-inserted" />
          <Stat label="Updated" value={result.rows_updated ?? 0} testId="result-updated" />
          <Stat label="Failed" value={result.rows_rejected ?? 0} tone="bad" testId="result-failed" />
          <Stat label="Duplicates" value={result.rows_duplicate ?? 0} testId="result-duplicates" />
          <Stat label="Left out" value={result.rows_excluded ?? 0} testId="result-excluded" />
        </StatRow>
        <p className="mt-2 text-xs text-muted-foreground">
          Used mapping version {result.mapping_version}.{" "}
          <a href={`/api/v1/files/${file.id}/download`} className="underline underline-offset-3">
            Download the original file
          </a>
          .
        </p>
      </section>

      {groups.length > 0 && (
        <section aria-label="Issues by cause">
          <h2 className="mb-2 text-sm font-semibold">What was found</h2>
          <IssueGroups groups={groups} fields={fileSchema?.fields} />
        </section>
      )}

      <RowIssues
        importId={importId}
        fileSchema={fileSchema}
        total={result.summary?.issues_total ?? 0}
      />
      {result.summary?.issues_truncated && (
        <p className="text-sm text-amber-800">
          This file had {formatCount(result.summary.issues_total)} issues. The counts above are
          complete, but only the first 20,000 are listed here, worst first.
        </p>
      )}

      {canOperate && (
        <Link href="/imports" className="inline-block text-sm underline underline-offset-3">
          Import another file
        </Link>
      )}
    </div>
  );
}
