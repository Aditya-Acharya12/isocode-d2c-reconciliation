"use client";

import { useQuery } from "@tanstack/react-query";

import { api, uploadFile } from "@/lib/api";

export type FileType =
  | "order"
  | "settlement"
  | "cod_remittance"
  | "freight_invoice"
  | "return_record"
  | "warehouse_receipt";

export type FileStage =
  | "analysing"
  | "analysis_failed"
  | "needs_mapping"
  | "importing"
  | "import_failed"
  | "imported";

export type JobStatus = "queued" | "running" | "succeeded" | "failed";
export type Severity = "error" | "warning" | "duplicate";

export interface SchemaField {
  name: string;
  label: string;
  kind: "id" | "text" | "money" | "date" | "int" | "enum" | "ref";
  required: boolean;
  enum_values: Record<string, string[]>;
}

export interface FileTypeSchema {
  file_type: FileType;
  label: string;
  uses_channel: boolean;
  allowed_partner_types: string[];
  any_of: string[][];
  fields: SchemaField[];
}

export interface IngestionSchema {
  date_formats: string[];
  file_types: FileTypeSchema[];
}

export interface ImportCounts {
  id: number;
  status: JobStatus;
  rows_total: number | null;
  rows_inserted: number | null;
  rows_updated: number | null;
  rows_duplicate: number | null;
  rows_rejected: number | null;
  rows_excluded: number | null;
  rows_warned: number | null;
  error: string | null;
  attempts: number;
  started_at: string | null;
  finished_at: string | null;
}

export interface UploadedFile {
  id: number;
  filename: string;
  file_type: FileType;
  partner_id: number | null;
  channel_id: number | null;
  source_name: string | null;
  size_bytes: number;
  sha256: string;
  uploaded_by: string;
  uploaded_at: string;
  is_synthetic: boolean;
  stage: FileStage;
  error: string | null;
  import_job: ImportCounts | null;
}

export interface ColumnProfile {
  samples: string[];
  distinct: string[];
  distinct_count: number;
  empty_count: number;
  date_format: string | null;
  date_formats: string[];
}

export interface Detection {
  kind: string;
  encoding: string | null;
  delimiter: string | null;
  sheet: string | null;
  sheets: string[];
  header_row: number;
  columns: string[];
  row_count: number;
  profiles: Record<string, ColumnProfile>;
  malformed_rows: boolean;
}

export interface FileDetail extends UploadedFile {
  detection: Detection | null;
}

export interface FilePage {
  items: UploadedFile[];
  total: number;
  limit: number;
  offset: number;
}

export interface DraftColumn {
  column: string;
  suggested_field: string | null;
  selected_field: string | null;
  confidence: number;
  reason: string;
  ambiguous: boolean;
  alternatives: string[];
  samples: string[];
  empty_count: number;
}

export interface FieldMapping {
  column: string;
  date_format?: string | null;
  value_map?: Record<string, string> | null;
  negate?: boolean;
}

export interface MappingDraft {
  provider: "gemini" | "heuristic" | "saved";
  note: string | null;
  saved_version: number | null;
  headers_changed: boolean;
  header_row: number;
  sheet: string | null;
  columns: DraftColumn[];
  field_details: Record<string, FieldMapping>;
}

export interface MappingSpec {
  sheet: string | null;
  header_row: number;
  fields: Record<string, FieldMapping>;
}

export interface IssueGroup {
  severity: Severity;
  code: string;
  field: string | null;
  message: string;
  count: number;
  first_rows: number[];
  examples: string[];
}

export interface PreviewRow {
  row_number: number;
  status: "ok" | Severity;
  values: Record<string, string>;
  canonical: Record<string, string>;
  issues: { field: string | null; code: string; severity: Severity; message: string }[];
}

export interface ValidationResult {
  rows_total: number;
  rows_valid: number;
  rows_invalid: number;
  rows_duplicate: number;
  rows_excluded: number;
  rows_warned: number;
  rows_existing: number;
  existing_behaviour: "update" | "skip";
  issue_groups: IssueGroup[];
  preview: PreviewRow[];
}

export interface Job<T = unknown> {
  id: number;
  kind: string;
  status: JobStatus;
  attempts: number;
  max_attempts: number;
  error: string | null;
  result: T | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface ImportDetail extends ImportCounts {
  file: UploadedFile;
  mapping_version: number;
  summary: { issue_groups: IssueGroup[]; issues_total: number; issues_truncated: boolean } | null;
  timings_ms: Record<string, number> | null;
}

export interface RowIssue {
  row_number: number;
  field: string | null;
  code: string;
  severity: Severity;
  message: string | null;
  raw_value: string | null;
}

export interface SavedMapping {
  id: number;
  file_type: FileType;
  source_name: string;
  active_version: number | null;
  version_count: number;
  updated_at: string;
}

export interface MappingVersion {
  id: number;
  version: number;
  is_active: boolean;
  source: "ai" | "heuristic" | "manual";
  spec: MappingSpec;
  approved_by: string;
  approved_at: string;
}

export function useIngestionSchema() {
  return useQuery({
    queryKey: ["ingestion-schema"],
    queryFn: () => api<IngestionSchema>("/ingestion/schema"),
    staleTime: Infinity,
  });
}

export function fileTypeLabel(schema: IngestionSchema | undefined, type: FileType): string {
  return schema?.file_types.find((item) => item.file_type === type)?.label ?? type;
}

/** The same normalising as the API, so that value suggestions agree. */
const normalise = (value: string) =>
  value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();

export function suggestValueMap(field: SchemaField, distinct: string[]): Record<string, string> {
  const known = new Map<string, string>();
  for (const [value, synonyms] of Object.entries(field.enum_values)) {
    for (const synonym of [...synonyms, value]) known.set(normalise(synonym), value);
  }
  const result: Record<string, string> = {};
  for (const raw of distinct) {
    const match = known.get(normalise(raw));
    if (match) result[raw] = match;
  }
  return result;
}

const ABBREVIATIONS: Record<string, string> = { cod: "COD", rto: "RTO" };

export const humanise = (value: string) => {
  if (ABBREVIATIONS[value]) return ABBREVIATIONS[value];
  const text = value.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
};

export { uploadFile };
