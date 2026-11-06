"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

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
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import {
  fileTypeLabel,
  useIngestionSchema,
  type MappingVersion,
  type SavedMapping,
} from "@/lib/ingestion";
import { useCan } from "@/lib/session";

const SOURCE_LABELS: Record<MappingVersion["source"], string> = {
  ai: "From an AI suggestion",
  heuristic: "From a rule-based suggestion",
  manual: "Set manually",
};

function Versions({ mapping, labelFor }: { mapping: SavedMapping; labelFor: (n: string) => string }) {
  const queryClient = useQueryClient();
  const canOperate = useCan("operate_reconciliation");
  const [restoring, setRestoring] = useState<MappingVersion | null>(null);
  const versions = useQuery({
    queryKey: ["mapping-versions", mapping.id],
    queryFn: () => api<MappingVersion[]>(`/mappings/${mapping.id}/versions`),
  });
  const restore = useMutation({
    mutationFn: (version: number) =>
      api<MappingVersion>(`/mappings/${mapping.id}/versions/${version}/restore`, {
        method: "POST",
      }),
    onSuccess: (created) => {
      toast.success(`Saved as version ${created.version}`);
      setRestoring(null);
      queryClient.invalidateQueries({ queryKey: ["mapping-versions", mapping.id] });
      queryClient.invalidateQueries({ queryKey: ["mappings"] });
    },
  });

  if (versions.isPending) return <RowsSkeleton rows={2} />;
  if (versions.isError) {
    return <ErrorState error={versions.error} onRetry={() => versions.refetch()} />;
  }

  return (
    <>
      <ul className="divide-y">
        {versions.data.map((version) => (
          <li key={version.id} className="py-3" data-testid={`version-${version.version}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm font-medium">
                Version {version.version}
                {version.is_active && (
                  <span className="ml-2 rounded bg-emerald-100 px-1.5 py-0.5 text-xs font-medium text-emerald-900">
                    Active
                  </span>
                )}
              </p>
              {canOperate && !version.is_active && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setRestoring(version)}
                  aria-label={`Restore version ${version.version}`}
                >
                  Restore
                </Button>
              )}
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {SOURCE_LABELS[version.source]}. Approved by {version.approved_by} on{" "}
              {formatDateTime(version.approved_at)}.
            </p>
            <dl className="mt-2 grid gap-x-6 gap-y-0.5 text-xs sm:grid-cols-2">
              {Object.entries(version.spec.fields).map(([name, field]) => (
                <div key={name} className="flex justify-between gap-3 border-b border-dashed py-0.5">
                  <dt className="truncate text-muted-foreground">{field.column}</dt>
                  <dd className="shrink-0 font-medium">{labelFor(name)}</dd>
                </div>
              ))}
            </dl>
          </li>
        ))}
      </ul>
      {restoring && (
        <Dialog open onOpenChange={(open) => !open && setRestoring(null)}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Restore version {restoring.version}?</DialogTitle>
              <DialogDescription>
                It&apos;s saved as a new version, and used for future files. History is kept, and
                data that&apos;s already imported doesn&apos;t change.
              </DialogDescription>
            </DialogHeader>
            {restore.isError && (
              <p role="alert" className="text-sm text-destructive">
                {restore.error.message}
              </p>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={() => setRestoring(null)}>
                Cancel
              </Button>
              <Button disabled={restore.isPending} onClick={() => restore.mutate(restoring.version)}>
                Restore
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}

export default function SavedMappingsPage() {
  const schema = useIngestionSchema();
  const [openId, setOpenId] = useState<number | null>(null);
  const mappings = useQuery({
    queryKey: ["mappings"],
    queryFn: () => api<SavedMapping[]>("/mappings"),
  });

  const labelFor = (fileType: SavedMapping["file_type"]) => (name: string) =>
    schema.data?.file_types
      .find((item) => item.file_type === fileType)
      ?.fields.find((field) => field.name === name)?.label ?? name;

  return (
    <>
      <PageHeader
        title="Saved mappings"
        description="Approved column mappings, by source and file type. Every version is kept."
      />
      {mappings.isPending && <RowsSkeleton />}
      {mappings.isError && <ErrorState error={mappings.error} onRetry={() => mappings.refetch()} />}
      {mappings.isSuccess && mappings.data.length === 0 && (
        <EmptyState title="No saved mappings yet">
          A mapping is saved here when it&apos;s approved during an import.
        </EmptyState>
      )}
      {mappings.isSuccess && mappings.data.length > 0 && (
        <ul className="divide-y rounded-md border">
          {mappings.data.map((mapping) => {
            const open = openId === mapping.id;
            return (
              <li key={mapping.id} data-testid={`mapping-${mapping.source_name}-${mapping.file_type}`}>
                <button
                  type="button"
                  aria-expanded={open}
                  onClick={() => setOpenId(open ? null : mapping.id)}
                  className="flex w-full flex-wrap items-center justify-between gap-2 px-4 py-3 text-left hover:bg-muted/50"
                >
                  <span className="text-sm font-medium">
                    {mapping.source_name}
                    <span className="font-normal text-muted-foreground">
                      {" "}
                      · {fileTypeLabel(schema.data, mapping.file_type)}
                    </span>
                  </span>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    Version {mapping.active_version} active, {mapping.version_count}{" "}
                    {mapping.version_count === 1 ? "version" : "versions"} in total
                  </span>
                </button>
                {open && (
                  <div className="border-t px-4 pb-1">
                    <Versions mapping={mapping} labelFor={labelFor(mapping.file_type)} />
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
