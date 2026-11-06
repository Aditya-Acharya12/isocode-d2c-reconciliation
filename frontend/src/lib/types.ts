export type Role = "finance_executive" | "co_founder";

export type Permission =
  | "view_workspace"
  | "view_config"
  | "manage_config"
  | "operate_reconciliation"
  | "manage_claims"
  | "view_all_audit"
  | "view_own_audit";

export interface Session {
  user: { id: number; email: string; full_name: string; role: Role };
  permissions: Permission[];
  demo_mode: boolean;
}

export interface WorkspaceSummary {
  partners: number;
  sales_channels: number;
  uploaded_files: number;
  orders: number;
  reconciliation_runs: number;
  open_claims: number;
}

export type PartnerType =
  | "marketplace"
  | "quick_commerce"
  | "courier"
  | "payment_gateway"
  | "warehouse";

export interface Partner {
  id: number;
  name: string;
  type: PartnerType;
  settlement_terms_days: number;
  return_grace_days: number;
  is_active: boolean;
  updated_at: string;
}

export interface AuditEntry {
  id: number;
  actor_email: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  created_at: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}
