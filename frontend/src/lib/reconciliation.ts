export type ReconStatus =
  | "settled"
  | "short_paid"
  | "unpaid_past_due"
  | "freight_overcharged"
  | "return_not_received"
  | "needs_review"
  | "pending";

export type Category =
  | "short_paid"
  | "unpaid_past_due"
  | "freight_overcharged"
  | "return_not_received";

export type ClaimStatus =
  | "draft"
  | "submitted"
  | "acknowledged"
  | "partially_recovered"
  | "recovered"
  | "rejected"
  | "written_off";

export const STATUS_LABELS: Record<ReconStatus, string> = {
  settled: "Settled",
  short_paid: "Short-paid",
  unpaid_past_due: "Unpaid past due",
  freight_overcharged: "Freight overcharged",
  return_not_received: "Return not received",
  needs_review: "Needs review",
  pending: "Pending",
};

export const STATUS_ORDER: ReconStatus[] = [
  "unpaid_past_due",
  "return_not_received",
  "short_paid",
  "freight_overcharged",
  "needs_review",
  "pending",
  "settled",
];

export const CATEGORY_LABELS: Record<Category, string> = {
  short_paid: "Short-paid",
  unpaid_past_due: "Unpaid past due",
  freight_overcharged: "Freight overcharged",
  return_not_received: "Return not received",
};

export const CLAIM_LABELS: Record<ClaimStatus, string> = {
  draft: "Draft",
  submitted: "Submitted",
  acknowledged: "Acknowledged",
  partially_recovered: "Partly recovered",
  recovered: "Recovered",
  rejected: "Rejected",
  written_off: "Written off",
};

const rupeeFormat = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** Formats integer paise without ever passing through a float division error. */
export function formatPaise(paise: number | null | undefined): string {
  if (paise === null || paise === undefined) return "–";
  const sign = paise < 0 ? "-" : "";
  const abs = Math.abs(paise);
  const whole = rupeeFormat.format(Math.trunc(abs / 100)).replace(/\.00$/, "");
  return `${sign}${whole}.${String(abs % 100).padStart(2, "0")}`;
}

/** Formats a decimal string from the API, such as "-37.00". */
export function formatAmount(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "None") return "–";
  const [whole, fraction = "00"] = value.replace("-", "").split(".");
  const sign = value.startsWith("-") ? "-" : "";
  return `${sign}${rupeeFormat.format(Number(whole)).replace(/\.00$/, "")}.${fraction.padEnd(2, "0").slice(0, 2)}`;
}

export function percent(bps: number): string {
  return `${(bps / 100).toLocaleString("en-IN", { maximumFractionDigits: 2 })}%`;
}

export interface RunInfo {
  id: number;
  as_of_date: string;
  status: "queued" | "running" | "succeeded" | "failed";
  counts: Record<string, unknown> | null;
  created_at: string;
  finished_at: string | null;
  error: string | null;
}

export interface Summary {
  orders_total: number;
  orders_evaluated: number;
  statuses: Record<ReconStatus, number>;
  reconciled: number;
  open_discrepancies: number;
  open_by_category: Record<string, { count: number; amount: string }>;
  money_at_risk: string;
  recovered_on_open: string;
  inventory_at_risk: string;
  unmatched_records: Record<string, number>;
  last_run: RunInfo | null;
}

export interface ResultRow {
  order_id: number;
  external_order_id: string;
  channel: string;
  order_date: string;
  order_value: string;
  payment_method: string;
  delivery_status: string;
  status: ReconStatus;
  expected_paise: number | null;
  actual_paise: number | null;
  difference_paise: number | null;
  reason: string | null;
  open_categories: Category[];
}
