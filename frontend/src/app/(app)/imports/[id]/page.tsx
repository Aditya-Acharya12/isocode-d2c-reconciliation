"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { ImportResult } from "@/components/imports/import-result";
import { MappingWorkflow } from "@/components/imports/mapping-workflow";
import { StagePill, Steps, Working } from "@/components/imports/status";
import { ErrorState, RowsSkeleton } from "@/components/states";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { formatBytes, formatDateTime } from "@/lib/format";
import { fileTypeLabel, useIngestionSchema, type FileDetail, type FileStage } from "@/lib/ingestion";
import { useCan } from "@/lib/session";

const STEP_FOR_STAGE: Record<FileStage, number> = {
  analysing: 0,
  analysis_failed: 0,
  needs_mapping: 1,
  importing: 3,
  import_failed: 3,
  imported: 4,
};

function DeleteUpload({ file }: { file: FileDetail }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const remove = useMutation({
    mutationFn: () => api<void>(`/files/${file.id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success(`${file.filename} deleted`);
      queryClient.invalidateQueries({ queryKey: ["files"] });
      router.push("/imports");
    },
  });
  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        Delete upload
      </Button>
      {open && (
        <Dialog open onOpenChange={(next) => !next && setOpen(false)}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Delete {file.filename}?</DialogTitle>
              <DialogDescription>
                It hasn&apos;t been imported, so no records are affected. You can upload it again,
                for example against the right partner.
              </DialogDescription>
            </DialogHeader>
            {remove.isError && (
              <p role="alert" className="text-sm text-destructive">
                {remove.error.message}
              </p>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={() => setOpen(false)}>
                Keep it
              </Button>
              <Button variant="destructive" disabled={remove.isPending} onClick={() => remove.mutate()}>
                Delete
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}

export default function ImportDetailPage() {
  const { id } = useParams<{ id: string }>();
  const canOperate = useCan("operate_reconciliation");
  const schema = useIngestionSchema();
  const [reviewing, setReviewing] = useState(false);
  const file = useQuery({
    queryKey: ["file", id],
    queryFn: () => api<FileDetail>(`/files/${id}`),
    // Polls only while a job is running, and stops as soon as it's done.
    refetchInterval: (query) =>
      ["analysing", "importing"].includes(query.state.data?.stage ?? "") ? 1500 : false,
  });

  if (file.isPending || schema.isPending) return <RowsSkeleton />;
  if (file.isError) return <ErrorState error={file.error} onRetry={() => file.refetch()} />;
  if (schema.isError) return <ErrorState error={schema.error} onRetry={() => schema.refetch()} />;

  const data = file.data;
  const fileSchema = schema.data.file_types.find((item) => item.file_type === data.file_type);
  const canDelete = canOperate && ["analysis_failed", "needs_mapping"].includes(data.stage);

  return (
    <>
      <nav className="mb-3 text-sm">
        <Link href="/imports" className="text-muted-foreground hover:text-foreground">
          ← Imports
        </Link>
      </nav>
      <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="flex flex-wrap items-center gap-2 text-xl font-semibold tracking-tight">
            <span className="break-all">{data.filename}</span>
            <StagePill stage={data.stage} />
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {fileTypeLabel(schema.data, data.file_type)} from {data.source_name}.{" "}
            {formatBytes(data.size_bytes)}, uploaded by {data.uploaded_by} on{" "}
            {formatDateTime(data.uploaded_at)}.
          </p>
        </div>
        {canDelete && <DeleteUpload file={data} />}
      </header>

      <Steps
        current={data.stage === "needs_mapping" && reviewing ? 2 : STEP_FOR_STAGE[data.stage]}
        done={data.stage === "imported"}
      />

      {data.stage === "analysing" && (
        <Working title="Reading the file…">
          Finding its columns and sampling its values. Larger files can take a little while.
        </Working>
      )}
      {data.stage === "analysis_failed" && (
        <div role="alert" className="rounded-md border border-destructive/30 p-4">
          <h2 className="text-sm font-semibold text-destructive">This file can&apos;t be used</h2>
          <p className="mt-1 text-sm" data-testid="analysis-error">
            {data.error}
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            Fix the file and upload it again.
          </p>
        </div>
      )}
      {data.stage === "needs_mapping" &&
        fileSchema &&
        (canOperate ? (
          <MappingWorkflow
            file={data}
            schema={schema.data}
            fileSchema={fileSchema}
            onReviewChange={setReviewing}
          />
        ) : (
          <div className="rounded-md border border-dashed px-6 py-10 text-center" data-testid="awaiting-finance">
            <p className="text-sm font-medium">Waiting for the finance team</p>
            <p className="mt-1 text-sm text-muted-foreground">
              A finance executive needs to review this file&apos;s mapping before it&apos;s imported.
            </p>
          </div>
        ))}
      {data.stage === "importing" && (
        <Working title="Importing…">
          You can leave this page. The import carries on, and appears in the history when it&apos;s
          done.
        </Working>
      )}
      {(data.stage === "imported" || data.stage === "import_failed") && (
        <ImportResult file={data} fileSchema={fileSchema} />
      )}
    </>
  );
}
