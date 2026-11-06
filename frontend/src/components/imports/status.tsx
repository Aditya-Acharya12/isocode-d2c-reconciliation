import { CheckIcon } from "lucide-react";
import type { ReactNode } from "react";

import { formatCount } from "@/lib/format";
import type { FileStage, IssueGroup, SchemaField, Severity } from "@/lib/ingestion";
import { cn } from "@/lib/utils";

const STAGE_STYLES: Record<FileStage, { label: string; className: string }> = {
  analysing: { label: "Analysing", className: "bg-muted text-muted-foreground" },
  analysis_failed: { label: "Can't be read", className: "bg-destructive/10 text-destructive" },
  needs_mapping: { label: "Needs mapping", className: "bg-amber-100 text-amber-900" },
  importing: { label: "Importing", className: "bg-muted text-muted-foreground" },
  import_failed: { label: "Import failed", className: "bg-destructive/10 text-destructive" },
  imported: { label: "Imported", className: "bg-emerald-100 text-emerald-900" },
};

export function StagePill({ stage }: { stage: FileStage }) {
  const { label, className } = STAGE_STYLES[stage];
  return (
    <span
      className={cn("inline-block rounded px-1.5 py-0.5 text-xs font-medium whitespace-nowrap", className)}
      data-testid="file-stage"
    >
      {label}
    </span>
  );
}

const SEVERITY_STYLES: Record<Severity | "ok", { label: string; className: string }> = {
  ok: { label: "OK", className: "bg-emerald-100 text-emerald-900" },
  error: { label: "Error", className: "bg-destructive/10 text-destructive" },
  warning: { label: "Warning", className: "bg-amber-100 text-amber-900" },
  duplicate: { label: "Duplicate", className: "bg-muted text-muted-foreground" },
};

export function SeverityPill({ severity }: { severity: Severity | "ok" }) {
  const { label, className } = SEVERITY_STYLES[severity];
  return (
    <span className={cn("inline-block rounded px-1.5 py-0.5 text-xs font-medium", className)}>
      {label}
    </span>
  );
}

export function Stat({
  label,
  value,
  tone,
  testId,
}: {
  label: string;
  value: number;
  tone?: "bad" | "good";
  testId?: string;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd
        data-testid={testId}
        className={cn(
          "mt-0.5 text-xl font-semibold tabular-nums",
          tone === "bad" && value > 0 && "text-destructive",
        )}
      >
        {formatCount(value)}
      </dd>
    </div>
  );
}

export function StatRow({ children }: { children: ReactNode }) {
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-4 rounded-md border p-4 sm:grid-cols-3 lg:grid-cols-6">
      {children}
    </dl>
  );
}

/** Issues grouped by cause, because fixing one cause often fixes thousands of rows. */
export function IssueGroups({ groups, fields }: { groups: IssueGroup[]; fields?: SchemaField[] }) {
  if (groups.length === 0) return null;
  const label = (name: string | null) =>
    name ? (fields?.find((field) => field.name === name)?.label ?? name) : null;
  return (
    <ul className="divide-y rounded-md border" data-testid="issue-groups">
      {groups.map((group) => (
        <li
          key={`${group.severity}:${group.code}:${group.field}`}
          className="flex flex-col gap-1 p-3 sm:flex-row sm:items-start sm:gap-3"
          data-code={group.code}
        >
          <div className="flex shrink-0 items-center gap-2 sm:w-36">
            <SeverityPill severity={group.severity} />
            <span className="text-sm font-medium tabular-nums">
              {formatCount(group.count)} {group.count === 1 ? "row" : "rows"}
            </span>
          </div>
          <div className="min-w-0 text-sm">
            <p>{group.message}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {label(group.field) && <span>{label(group.field)}. </span>}
              Row{group.first_rows.length > 1 ? "s" : ""} {group.first_rows.join(", ")}
              {group.count > group.first_rows.length ? " and more" : ""}
              {group.examples.length > 0 && (
                <span className="break-all">
                  . For example: {group.examples.map((value) => `"${value}"`).join(", ")}
                </span>
              )}
            </p>
          </div>
        </li>
      ))}
    </ul>
  );
}

const STEPS = ["Upload", "Map columns", "Review", "Import"];

export function Steps({ current, done = false }: { current: number; done?: boolean }) {
  return (
    <ol className="mb-8 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm" aria-label="Progress">
      {STEPS.map((step, index) => {
        const complete = done || index < current;
        const active = !done && index === current;
        return (
          <li
            key={step}
            aria-current={active ? "step" : undefined}
            className={cn(
              "flex items-center gap-1.5",
              active ? "font-medium" : complete ? "text-foreground" : "text-muted-foreground",
            )}
          >
            <span
              className={cn(
                "flex size-5 items-center justify-center rounded-full border text-xs tabular-nums",
                complete && "border-foreground bg-foreground text-background",
                active && "border-foreground",
              )}
            >
              {complete ? <CheckIcon className="size-3" /> : index + 1}
            </span>
            {step}
          </li>
        );
      })}
    </ol>
  );
}

export function Working({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div role="status" className="rounded-md border px-6 py-10 text-center" data-testid="working">
      <div
        aria-hidden
        className="mx-auto mb-3 size-5 animate-spin rounded-full border-2 border-muted border-t-foreground"
      />
      <p className="text-sm font-medium">{title}</p>
      {children && <p className="mt-1 text-sm text-muted-foreground">{children}</p>}
    </div>
  );
}
