"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { IssueGroups, SeverityPill, Stat, StatRow, Working } from "@/components/imports/status";
import { NativeSelect } from "@/components/native-select";
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
  humanise,
  suggestValueMap,
  type DraftColumn,
  type FieldMapping,
  type FileDetail,
  type FileTypeSchema,
  type IngestionSchema,
  type Job,
  type MappingDraft,
  type MappingSpec,
  type SchemaField,
  type ValidationResult,
} from "@/lib/ingestion";
import { cn } from "@/lib/utils";

type Details = Record<string, Omit<FieldMapping, "column">>;

const PROVIDER_NOTES: Record<MappingDraft["provider"], string> = {
  gemini: "Suggested by Gemini AI. Nothing is applied until you approve it.",
  heuristic: "Suggested by rule-based matching on column names and values. Review them below.",
  saved: "Loaded from your saved mapping.",
};

function ConfidenceBadge({ column, provider }: { column: DraftColumn; provider: string }) {
  if (!column.suggested_field) {
    return column.ambiguous ? (
      <span className="text-xs text-amber-800">Ambiguous</span>
    ) : (
      <span className="text-xs text-muted-foreground">No match</span>
    );
  }
  if (provider === "saved") return <span className="text-xs text-muted-foreground">Saved</span>;
  const percent = Math.round(column.confidence * 100);
  return (
    <span
      data-testid={`confidence-${column.column}`}
      className={cn(
        "text-xs font-medium tabular-nums",
        column.ambiguous || percent < 80 ? "text-amber-800" : "text-emerald-800",
      )}
    >
      {percent}%{column.ambiguous ? ", ambiguous" : ""}
    </span>
  );
}

function initialDetails(draft: MappingDraft): Details {
  const details: Details = {};
  for (const [name, mapping] of Object.entries(draft.field_details)) {
    details[name] = {
      date_format: mapping.date_format ?? null,
      value_map: mapping.value_map ?? null,
      negate: mapping.negate ?? false,
    };
  }
  return details;
}

function MappingForm({
  file,
  schema,
  fileSchema,
  draft,
  onReviewChange,
}: {
  file: FileDetail;
  schema: IngestionSchema;
  fileSchema: FileTypeSchema;
  draft: MappingDraft;
  onReviewChange: (reviewing: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const detection = file.detection!;
  const [selections, setSelections] = useState<Record<string, string>>(() =>
    Object.fromEntries(draft.columns.map((c) => [c.column, c.selected_field ?? ""])),
  );
  const [details, setDetails] = useState<Details>(() => initialDetails(draft));
  const [validationJobId, setValidationJobId] = useState<number | null>(null);
  const [validatedKey, setValidatedKey] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const fieldByName = useMemo(
    () => new Map(fileSchema.fields.map((field) => [field.name, field])),
    [fileSchema],
  );
  const columnByField = useMemo(() => {
    const result = new Map<string, string>();
    for (const [column, field] of Object.entries(selections)) {
      if (field) result.set(field, column);
    }
    return result;
  }, [selections]);

  const spec: MappingSpec = useMemo(() => {
    const fields: MappingSpec["fields"] = {};
    for (const [name, column] of columnByField) {
      const field = fieldByName.get(name);
      const detail = details[name] ?? {};
      fields[name] = {
        column,
        date_format: field?.kind === "date" ? (detail.date_format ?? null) : null,
        value_map: field?.kind === "enum" ? (detail.value_map ?? {}) : null,
        negate: field?.kind === "money" ? Boolean(detail.negate) : false,
      };
    }
    return { sheet: draft.sheet, header_row: draft.header_row, fields };
  }, [columnByField, details, draft, fieldByName]);
  const specKey = JSON.stringify(spec);

  const usedBy = (field: string, column: string) => {
    const owner = columnByField.get(field);
    return owner !== undefined && owner !== column ? owner : null;
  };

  function selectField(column: string, name: string) {
    setSelections((current) => {
      const next = { ...current };
      // A field can only come from one column, so taking it releases the other.
      if (name) {
        for (const [other, field] of Object.entries(next)) {
          if (field === name) next[other] = "";
        }
      }
      next[column] = name;
      return next;
    });
    const field = fieldByName.get(name);
    if (!field) return;
    const profile = detection.profiles[column];
    setDetails((current) => ({
      ...current,
      [name]: {
        date_format: field.kind === "date" ? (profile?.date_format ?? null) : null,
        value_map: field.kind === "enum" ? suggestValueMap(field, profile?.distinct ?? []) : null,
        negate: false,
      },
    }));
  }

  function updateDetail(name: string, patch: Partial<Details[string]>) {
    setDetails((current) => ({ ...current, [name]: { ...current[name], ...patch } }));
  }

  const missingRequired = fileSchema.fields.filter(
    (field) => field.required && !columnByField.has(field.name),
  );
  const missingGroups = fileSchema.any_of.filter(
    (group) => !group.some((name) => columnByField.has(name)),
  );
  const missingFormats = [...columnByField.keys()].filter((name) => {
    const field = fieldByName.get(name);
    return field?.kind === "date" && !details[name]?.date_format;
  });
  const blockers = [
    ...missingRequired.map((field) => `Map a column to ${field.label}.`),
    ...missingGroups.map(
      (group) =>
        `Map ${group.map((name) => fieldByName.get(name)?.label).join(" or ")}, so that records can be linked to orders.`,
    ),
    ...missingFormats.map((name) => `Choose a date format for ${fieldByName.get(name)?.label}.`),
  ];

  const validate = useMutation({
    mutationFn: () =>
      api<Job<ValidationResult>>(`/files/${file.id}/validations`, {
        method: "POST",
        body: { spec },
      }),
    onSuccess: (job) => {
      setValidationJobId(job.id);
      setValidatedKey(specKey);
    },
  });

  const validation = useQuery({
    queryKey: ["job", validationJobId],
    enabled: validationJobId !== null,
    queryFn: () => api<Job<ValidationResult>>(`/jobs/${validationJobId}`),
    refetchInterval: (query) =>
      ["queued", "running"].includes(query.state.data?.status ?? "queued") ? 1500 : false,
  });

  // A changed mapping makes the earlier result meaningless, so it can't be acted on.
  const isCurrent = validatedKey === specKey && validationJobId !== null;
  const job = isCurrent ? validation.data : undefined;
  const result = job?.status === "succeeded" ? job.result : null;
  const validating =
    validate.isPending || (isCurrent && (!job || ["queued", "running"].includes(job.status)));

  const reviewing = Boolean(result);
  useEffect(() => {
    onReviewChange(reviewing);
    if (reviewing) {
      document.getElementById("validation-summary")?.scrollIntoView({ behavior: "smooth" });
    }
  }, [reviewing, onReviewChange]);

  const startImport = useMutation({
    mutationFn: () =>
      api<{ import_id: number; created: boolean }>(`/files/${file.id}/imports`, {
        method: "POST",
        retrySafe: true, // the API treats a repeat as the same import
        body: {
          spec,
          validation_job_id: validationJobId,
          acknowledged_invalid_rows: result?.rows_invalid ?? 0,
          suggested_by: draft.provider,
          suggestion: {
            provider: draft.provider,
            columns: draft.columns.map((column) => ({
              column: column.column,
              field: column.suggested_field,
              confidence: column.confidence,
            })),
          },
        },
      }),
    onSuccess: () => {
      toast.success("Mapping approved and import started");
      setConfirming(false);
      queryClient.invalidateQueries({ queryKey: ["file", String(file.id)] });
      queryClient.invalidateQueries({ queryKey: ["files"] });
    },
  });

  const enumFields = [...columnByField.keys()]
    .map((name) => fieldByName.get(name))
    .filter((field): field is SchemaField => field?.kind === "enum");

  return (
    <div className="space-y-8">
      <section aria-label="Column mapping">
        <div className="mb-3">
          <h2 className="text-sm font-semibold">Map the columns</h2>
          <p className="mt-0.5 text-sm text-muted-foreground" data-testid="provider-note">
            {draft.note ?? PROVIDER_NOTES[draft.provider]}
            {draft.provider === "saved" ? ` (version ${draft.saved_version})` : ""}
          </p>
          {draft.headers_changed && (
            <p className="mt-1 text-sm text-amber-800" data-testid="headers-changed">
              This file&apos;s headers differ from your saved mapping, so please review it again.
            </p>
          )}
          <p className="mt-1 text-xs text-muted-foreground">
            {formatCount(detection.row_count)} rows. Headers found on row {draft.header_row + 1}
            {detection.sheet ? `, in sheet "${detection.sheet}"` : ""}.
          </p>
        </div>

        <div className="overflow-x-auto rounded-md border">
          <Table data-testid="mapping-table">
            <TableHeader>
              <TableRow>
                <TableHead>Column in your file</TableHead>
                <TableHead className="min-w-48">Import as</TableHead>
                <TableHead>Confidence</TableHead>
                <TableHead className="min-w-64">Why</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {draft.columns.map((column) => {
                const selected = selections[column.column];
                const field = fieldByName.get(selected);
                const profile = detection.profiles[column.column];
                const needsLook = column.ambiguous || (column.suggested_field && !selected);
                return (
                  <TableRow
                    key={column.column}
                    data-testid={`mapping-row-${column.column}`}
                    className={needsLook && !selected ? "bg-amber-50/60" : undefined}
                  >
                    <TableCell className="max-w-56 align-top whitespace-normal">
                      <p className="font-medium break-words">{column.column}</p>
                      <p className="mt-0.5 truncate text-xs text-muted-foreground">
                        {column.samples.slice(0, 3).join(", ") || "Empty column"}
                      </p>
                    </TableCell>
                    <TableCell className="align-top">
                      <NativeSelect
                        aria-label={`Import ${column.column} as`}
                        value={selected}
                        onChange={(event) => selectField(column.column, event.target.value)}
                      >
                        <option value="">Don&apos;t import</option>
                        {fileSchema.fields.map((option) => {
                          const owner = usedBy(option.name, column.column);
                          return (
                            <option key={option.name} value={option.name}>
                              {option.label}
                              {option.required ? " (required)" : ""}
                              {owner ? ` (now from ${owner})` : ""}
                            </option>
                          );
                        })}
                      </NativeSelect>
                      {field?.kind === "date" && (
                        <div className="mt-2">
                          <NativeSelect
                            aria-label={`Date format for ${column.column}`}
                            aria-invalid={!details[selected]?.date_format}
                            value={details[selected]?.date_format ?? ""}
                            onChange={(event) =>
                              updateDetail(selected, { date_format: event.target.value || null })
                            }
                          >
                            <option value="">Choose a date format</option>
                            {schema.date_formats.map((format) => (
                              <option key={format} value={format}>
                                {format}
                              </option>
                            ))}
                          </NativeSelect>
                          {(profile?.date_formats?.length ?? 0) > 1 && (
                            <p className="mt-1 text-xs text-amber-800 whitespace-normal">
                              These dates also fit {profile.date_formats
                                .filter((format) => format !== details[selected]?.date_format)
                                .slice(0, 2)
                                .join(" and ")}
                              . Please check it&apos;s right.
                            </p>
                          )}
                        </div>
                      )}
                      {field?.kind === "money" && (
                        <label className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
                          <input
                            type="checkbox"
                            checked={Boolean(details[selected]?.negate)}
                            onChange={(event) =>
                              updateDetail(selected, { negate: event.target.checked })
                            }
                          />
                          Reverse the sign
                        </label>
                      )}
                    </TableCell>
                    <TableCell className="align-top">
                      <ConfidenceBadge column={column} provider={draft.provider} />
                    </TableCell>
                    <TableCell className="align-top text-xs text-muted-foreground whitespace-normal">
                      {column.reason}
                      {column.suggested_field && !selected && (
                        <div className="mt-1 text-amber-800">
                          Suggested: {fieldByName.get(column.suggested_field)?.label}. Not selected
                          because it needs your confirmation.
                        </div>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </section>

      {enumFields.length > 0 && (
        <section aria-label="Value translations">
          <h2 className="text-sm font-semibold">Translate values</h2>
          <p className="mt-0.5 mb-3 text-sm text-muted-foreground">
            Partners write these differently. Rows with an untranslated value won&apos;t import.
          </p>
          <div className="grid gap-4 lg:grid-cols-2">
            {enumFields.map((field) => {
              const column = columnByField.get(field.name) as string;
              const profile = detection.profiles[column];
              const map = details[field.name]?.value_map ?? {};
              return (
                <div key={field.name} className="rounded-md border p-3" data-testid={`values-${field.name}`}>
                  <p className="text-sm font-medium">{field.label}</p>
                  <p className="mb-2 text-xs text-muted-foreground">From &quot;{column}&quot;</p>
                  <ul className="space-y-1.5">
                    {(profile?.distinct ?? []).map((raw) => (
                      <li key={raw} className="grid grid-cols-2 items-center gap-2">
                        <span className="truncate text-sm" title={raw}>
                          {raw}
                        </span>
                        <NativeSelect
                          aria-label={`${field.label} value ${raw}`}
                          aria-invalid={!map[raw]}
                          value={map[raw] ?? ""}
                          onChange={(event) => {
                            const next = { ...map };
                            if (event.target.value) next[raw] = event.target.value;
                            else delete next[raw];
                            updateDetail(field.name, { value_map: next });
                          }}
                        >
                          <option value="">Not translated</option>
                          {Object.keys(field.enum_values).map((value) => (
                            <option key={value} value={value}>
                              {humanise(value)}
                            </option>
                          ))}
                        </NativeSelect>
                      </li>
                    ))}
                  </ul>
                  {(profile?.distinct_count ?? 0) > (profile?.distinct.length ?? 0) && (
                    <p className="mt-2 text-xs text-amber-800">
                      This column has {formatCount(profile.distinct_count)} different values. Only
                      the most common are shown, and rows with others won&apos;t import.
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}

      <section aria-label="Validate" className="rounded-md border p-4">
        {blockers.length > 0 ? (
          <ul className="mb-3 space-y-1 text-sm text-destructive" data-testid="mapping-blockers">
            {blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
        ) : (
          <p className="mb-3 text-sm text-muted-foreground">
            Every required field is mapped. Validation checks all{" "}
            {formatCount(detection.row_count)} rows, and changes nothing.
          </p>
        )}
        <Button
          variant={result ? "outline" : "default"}
          disabled={blockers.length > 0 || validating}
          onClick={() => validate.mutate()}
        >
          {validating ? "Validating…" : "Validate these rows"}
        </Button>
        {validate.isError && (
          <p role="alert" className="mt-2 text-sm text-destructive">
            {validate.error.message}
          </p>
        )}
        {validatedKey !== null && !isCurrent && (
          <p className="mt-2 text-sm text-amber-800" data-testid="stale-validation">
            You changed the mapping, so please validate it again.
          </p>
        )}
      </section>

      {validating && <Working title="Checking every row…">This doesn&apos;t change any data.</Working>}
      {validation.isError && isCurrent && (
        <ErrorState error={validation.error} onRetry={() => validation.refetch()} />
      )}
      {job?.status === "failed" && (
        <p role="alert" className="text-sm text-destructive">
          {job.error}
        </p>
      )}

      {result && (
        <section id="validation-summary" aria-label="Validation summary" className="space-y-6">
          <div>
            <h2 className="mb-3 text-sm font-semibold">Validation summary</h2>
            <StatRow>
              <Stat label="Rows checked" value={result.rows_total} testId="stat-total" />
              <Stat label="Ready to import" value={result.rows_valid} testId="stat-valid" />
              <Stat label="Invalid" value={result.rows_invalid} tone="bad" testId="stat-invalid" />
              <Stat label="Duplicates in file" value={result.rows_duplicate} testId="stat-duplicate" />
              <Stat label="With warnings" value={result.rows_warned} testId="stat-warned" />
              <Stat label="Left out" value={result.rows_excluded} testId="stat-excluded" />
            </StatRow>
            {result.rows_existing > 0 && (
              <p className="mt-2 text-sm text-muted-foreground" data-testid="existing-note">
                {formatCount(result.rows_existing)} of these are already in the system. They{" "}
                {result.existing_behaviour === "update"
                  ? "will update the existing orders if anything changed."
                  : "will be skipped, so nothing is counted twice."}
              </p>
            )}
          </div>

          {result.issue_groups.length > 0 && (
            <div>
              <h3 className="mb-2 text-sm font-semibold">What was found</h3>
              <IssueGroups groups={result.issue_groups} fields={fileSchema.fields} />
            </div>
          )}

          <div>
            <h3 className="mb-2 text-sm font-semibold">Preview</h3>
            <div className="overflow-x-auto rounded-md border">
              <Table data-testid="preview-table">
                <TableHeader>
                  <TableRow>
                    <TableHead>Row</TableHead>
                    <TableHead>Status</TableHead>
                    {Object.entries(spec.fields).map(([name, mapping]) => (
                      <TableHead key={name}>
                        <div>{fieldByName.get(name)?.label}</div>
                        <div className="text-xs font-normal text-muted-foreground">
                          from {mapping.column}
                        </div>
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {result.preview.map((row) => {
                    const badFields = new Set(
                      row.issues.filter((i) => i.severity === "error").map((i) => i.field),
                    );
                    return (
                      <TableRow key={row.row_number} data-testid={`preview-row-${row.row_number}`}>
                        <TableCell className="text-muted-foreground tabular-nums">
                          {row.row_number}
                        </TableCell>
                        <TableCell>
                          <SeverityPill severity={row.status} />
                        </TableCell>
                        {Object.keys(spec.fields).map((name) => (
                          <TableCell
                            key={name}
                            className={cn(
                              "max-w-48 truncate",
                              badFields.has(name) && "bg-destructive/5 font-medium text-destructive",
                            )}
                            title={row.issues.find((i) => i.field === name)?.message}
                          >
                            {badFields.has(name)
                              ? row.values[name] || "(empty)"
                              : fieldByName.get(name)?.kind === "ref"
                                ? row.values[name]
                                : fieldByName.get(name)?.kind === "enum"
                                  ? humanise(row.canonical[name] ?? "")
                                  : row.canonical[name] || row.values[name]}
                          </TableCell>
                        ))}
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              The first rows of the file, plus the first rows with problems.
            </p>
          </div>

          <div className="rounded-md border p-4">
            <h3 className="text-sm font-semibold">Approve this mapping</h3>
            {result.rows_valid === 0 ? (
              <p className="mt-1 text-sm text-destructive" data-testid="nothing-to-import">
                No rows can be imported with this mapping. Fix the file or the mapping, and validate
                again.
              </p>
            ) : (
              <p className="mt-1 text-sm text-muted-foreground">
                {formatCount(result.rows_valid)} rows will be imported, and this mapping will be
                saved for future files from {file.source_name}.
              </p>
            )}
            <Button
              className="mt-3"
              disabled={result.rows_valid === 0 || startImport.isPending}
              onClick={() => (result.rows_invalid > 0 ? setConfirming(true) : startImport.mutate())}
            >
              {startImport.isPending ? "Starting…" : "Approve and import"}
            </Button>
            {startImport.isError && !confirming && (
              <p role="alert" className="mt-2 text-sm text-destructive">
                {startImport.error.message}
              </p>
            )}
          </div>
        </section>
      )}

      {result && confirming && (
        <Dialog open onOpenChange={(open) => !open && setConfirming(false)}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Import without the invalid rows?</DialogTitle>
              <DialogDescription>
                {formatCount(result.rows_invalid)} rows have errors and won&apos;t be imported. You
                can inspect and download them afterwards, fix them, and upload them as a new file.
              </DialogDescription>
            </DialogHeader>
            {startImport.isError && (
              <p role="alert" className="text-sm text-destructive">
                {startImport.error.message}
              </p>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={() => setConfirming(false)}>
                Go back
              </Button>
              <Button disabled={startImport.isPending} onClick={() => startImport.mutate()}>
                Import {formatCount(result.rows_valid)} rows
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}

export function MappingWorkflow({
  file,
  schema,
  fileSchema,
  onReviewChange,
}: {
  file: FileDetail;
  schema: IngestionSchema;
  fileSchema: FileTypeSchema;
  onReviewChange: (reviewing: boolean) => void;
}) {
  const draft = useQuery({
    queryKey: ["mapping-draft", file.id],
    queryFn: () =>
      api<MappingDraft>(`/files/${file.id}/mapping-suggestions`, {
        method: "POST",
        retrySafe: true, // it only reads
      }),
    staleTime: Infinity,
  });

  if (draft.isPending) return <RowsSkeleton />;
  if (draft.isError) return <ErrorState error={draft.error} onRetry={() => draft.refetch()} />;
  return (
    <MappingForm
      file={file}
      schema={schema}
      fileSchema={fileSchema}
      draft={draft.data}
      onReviewChange={onReviewChange}
    />
  );
}
