"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { StagePill } from "@/components/imports/status";
import { UploadPanel } from "@/components/imports/upload-panel";
import { NativeSelect } from "@/components/native-select";
import { EmptyState, ErrorState, PageHeader, RowsSkeleton } from "@/components/states";
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
import { formatCount, formatDateTime } from "@/lib/format";
import { fileTypeLabel, useIngestionSchema, type FilePage, type FileType } from "@/lib/ingestion";
import { useCan } from "@/lib/session";

const PAGE_SIZE = 20;

function count(value: number | null | undefined) {
  return value === null || value === undefined ? "–" : formatCount(value);
}

export default function ImportsPage() {
  const canOperate = useCan("operate_reconciliation");
  const schema = useIngestionSchema();
  const [offset, setOffset] = useState(0);
  const [fileType, setFileType] = useState<FileType | "">("");

  const files = useQuery({
    queryKey: ["files", fileType, offset],
    queryFn: () =>
      api<FilePage>(
        `/files?limit=${PAGE_SIZE}&offset=${offset}${fileType ? `&file_type=${fileType}` : ""}`,
      ),
    placeholderData: keepPreviousData,
    // Only polls while something is running, so an idle page doesn't wake the database.
    refetchInterval: (query) =>
      query.state.data?.items.some((item) => ["analysing", "importing"].includes(item.stage))
        ? 3000
        : false,
  });

  return (
    <>
      <PageHeader
        title="Imports"
        description={
          canOperate
            ? "Upload partner files, check how their columns map, and import them."
            : "Files uploaded by the finance team, and how each import went."
        }
      />

      {canOperate && schema.isSuccess && (
        <section className="mb-10" aria-label="Upload a file">
          <UploadPanel schema={schema.data} />
        </section>
      )}
      {schema.isError && <ErrorState error={schema.error} onRetry={() => schema.refetch()} />}

      <section aria-label="Import history">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <h2 className="text-sm font-semibold">History</h2>
          <div className="flex items-center gap-2">
            <Label htmlFor="history-filter" className="text-xs text-muted-foreground">
              Category
            </Label>
            <NativeSelect
              id="history-filter"
              className="w-44"
              value={fileType}
              onChange={(event) => {
                setFileType(event.target.value as FileType | "");
                setOffset(0);
              }}
            >
              <option value="">All categories</option>
              {schema.data?.file_types.map((item) => (
                <option key={item.file_type} value={item.file_type}>
                  {item.label}
                </option>
              ))}
            </NativeSelect>
          </div>
        </div>

        {files.isPending && <RowsSkeleton />}
        {files.isError && <ErrorState error={files.error} onRetry={() => files.refetch()} />}
        {files.isSuccess && files.data.items.length === 0 && (
          <EmptyState title={fileType ? "No files in this category" : "No files uploaded yet"}>
            {canOperate && !fileType && "Upload a partner file above to get started."}
          </EmptyState>
        )}
        {files.isSuccess && files.data.items.length > 0 && (
          <>
            <div className="overflow-x-auto rounded-md border">
              <Table data-testid="history-table">
                <TableHeader>
                  <TableRow>
                    <TableHead>File</TableHead>
                    <TableHead>Source and category</TableHead>
                    <TableHead>Uploaded</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Rows</TableHead>
                    <TableHead className="text-right">Imported</TableHead>
                    <TableHead className="text-right">Failed</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {files.data.items.map((file) => {
                    const job = file.import_job;
                    const imported =
                      job && job.rows_inserted !== null
                        ? job.rows_inserted + (job.rows_updated ?? 0)
                        : null;
                    return (
                      <TableRow key={file.id} data-testid={`file-row-${file.filename}`}>
                        <TableCell className="max-w-48 truncate font-medium">
                          <Link
                            href={`/imports/${file.id}`}
                            className="underline-offset-3 hover:underline"
                            title={file.filename}
                          >
                            {file.filename}
                          </Link>
                        </TableCell>
                        <TableCell className="max-w-48 truncate">
                          <div className="truncate" title={file.source_name ?? undefined}>
                            {file.source_name}
                          </div>
                          <div className="text-xs text-muted-foreground">
                            {fileTypeLabel(schema.data, file.file_type)}
                          </div>
                        </TableCell>
                        <TableCell className="max-w-48">
                          <div className="whitespace-nowrap tabular-nums">
                            {formatDateTime(file.uploaded_at)}
                          </div>
                          <div
                            className="truncate text-xs text-muted-foreground"
                            title={file.uploaded_by}
                          >
                            {file.uploaded_by}
                          </div>
                        </TableCell>
                        <TableCell>
                          <StagePill stage={file.stage} />
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {count(job?.rows_total)}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{count(imported)}</TableCell>
                        <TableCell
                          className={
                            job?.rows_rejected
                              ? "text-right font-medium text-destructive tabular-nums"
                              : "text-right tabular-nums"
                          }
                        >
                          {count(job?.rows_rejected)}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
            <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
              <span className="tabular-nums">
                {formatCount(offset + 1)}–{formatCount(offset + files.data.items.length)} of{" "}
                {formatCount(files.data.total)}
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
                  disabled={offset + PAGE_SIZE >= files.data.total}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                >
                  Next
                </Button>
              </div>
            </div>
          </>
        )}
      </section>
    </>
  );
}
