# Architecture

Status: approved direction, implementation not started. Hosting is limited to free tiers, and the deadline is 9 November 2026, 11:59 PM IST. Assumption IDs such as A3 refer to [assumptions.md](assumptions.md).

## 1. System overview

The system is a single FastAPI application with a Postgres-backed job table, plus a Next.js frontend. There are no microservices, Redis, Celery or object storage.

```
Browser
   |
Next.js on Vercel Hobby -- same-origin /api/* rewrite --> FastAPI on Render Free
                                                            |-- API routers (auth, RBAC, validation)
                                                            |-- domain services (pure logic, no HTTP)
                                                            |     ingestion / matching / rates / recon / claims
                                                            |-- mapping suggester: heuristic | Gemini
                                                            |-- one background job thread
                                                            |
                                     pooled URL (API) ------+------ direct URL (jobs, migrations)
                                                            |
                                                     Neon Free PostgreSQL
                                                     (data, jobs, compressed uploaded files)
```

| Decision | Why | Trade-off |
|---|---|---|
| Monolith with clear layers | One developer, four days | Workers can't scale separately. That's fine at 30k orders a month |
| Postgres `import_jobs` table as the job queue | Job state survives restarts and no extra infrastructure is needed | Lower throughput than a real broker, which this load doesn't need |
| One in-process job thread, running one job at a time | Simplest option that can be tested by the deadline. A second process would load pandas again on a small free instance | A heavy job can slow API responses, and jobs can't run in parallel |
| Pandas for parsing, `COPY` for loading, SQL for aggregation | The only realistic way to reach 100k rows in under a minute | Less ORM use in the bulk paths |
| Engine calculates in integer paise; database stores `NUMERIC(14,2)` | Floats must never touch money | Conversion is needed at the boundary |
| Uploaded files gzip-compressed in a `bytea` table | Render's filesystem is wiped on restart, and evidence must survive | Uses Neon's 1 GB allowance, so uploads have a size limit |
| Pure-function rule evaluation | Financial rules can be tested without a database | Requires discipline about layer boundaries |

### Authentication

Passwords are hashed with Argon2. Access tokens are short-lived JWTs in an httpOnly, SameSite=Lax cookie. Next.js rewrites `/api/*` to the backend, so the browser sees one origin and third-party cookie blocking doesn't apply.

Vercel's documentation pages show proxied request timeouts of both 30 and 120 seconds, so the design assumes 30. Every long-running operation returns a job ID and the frontend polls for it. Vercel documents a 4.5 MB body limit for Functions, but I couldn't confirm whether it also applies to external rewrites. **This gets tested in Milestone 1.** If it does apply, uploads go directly to the API using a short-lived, upload-only token.

## 2. Roles

Two roles are stored as an enum on `users`. A separate role table would add nothing with a fixed pair of roles.

| Capability | Finance Executive | Co-founder |
|---|---|---|
| View dashboard, reconciliation, claims, reports | Yes | Yes |
| Upload files, approve mappings, run reconciliation | Yes | No |
| Create and update claims, resolve the match review queue | Yes | No |
| Edit partners, rate cards, tax rules, settings | Read only | Yes |
| View audit log | Own actions | All |

Rate cards decide what counts as leakage, so the person reconciling can't edit them. Roles are enforced on the backend; hiding a button in the UI is only a convenience.

## 3. Canonical data model

Mappings translate partner columns into these fields. `*` means required.

| File type | Fields |
|---|---|
| **Order** | `order_id*`, `channel*`, `order_date*`, `order_value*`, `payment_method*`, `delivery_status*`, `tracking_id`, `courier`, `delivered_at`, `returned_at`, `weight_grams`, `weight_slab`, `zone`, `quantity` |
| **Settlement** | `settlement_ref*`, `net_amount*`, `settlement_date*`, `order_id` or `partner_order_ref`†, `gross_amount`, `commission`, `fees`, `tcs_amount`, `tds_amount`, `transaction_type`, `utr` |
| **COD remittance** | `tracking_id` or `order_id`†, `net_remittance*`, `remittance_date*`, `cod_amount`, `cod_fee`, `remittance_ref`, `utr` |
| **Freight invoice** | `invoice_no*`, `tracking_id*`, `total_charge*`, `invoice_date*`, `shipment_type`, `billed_weight_grams`, `weight_slab`, `zone`, `base_freight`, `cod_charge`, `other_charges`, `gst_on_freight`, `order_id` |
| **Return record** | `tracking_id` or `order_id`†, `return_type*`, `returned_at*`, `return_status`, `quantity`, `return_value` |
| **Warehouse receipt** | `receipt_ref*`, `received_at*`, `tracking_id` or `order_id`†, `quantity_received`, `condition` |

† At least one is required. A record with neither can't be matched and goes to the review queue, provided the file has enough other data to find candidates.

A mapping also stores what a column name can't express:

- The date format, such as `DD/MM/YYYY`
- The sign convention for refunds and debits
- A value map for enum fields, such as `DLVD` → `delivered`
- Header row and sheet name

An unmapped enum value makes the row invalid. It is never guessed.

## 4. Database schema

All tables have `id` and `created_at`; mutable tables also have `updated_at`.

### Identity and configuration

| Table | Key columns | Constraints and notes |
|---|---|---|
| `users` | `email`, `password_hash`, `role`, `is_active` | Unique `email` |
| `partners` | `name`, `type` (marketplace, quick_commerce, courier, payment_gateway), `settlement_terms_days`, `return_grace_days` | Unique `name` |
| `sales_channels` | `code`, `name`, `partner_id` (null for Website) | Unique `code` |
| `rate_cards` | `name`, `version`, `effective_from`, `effective_to`, `status` | Published cards are immutable, so changes create a new version. No overlapping dates within a scope |
| `settlement_rate_rules` | `rate_card_id`, `channel_id`, `payment_method`, `event` (delivered, rto, customer_return), `payer_partner_id` (null means the order's courier), `pct_bps`, `fixed_paise`, `rounding` | Unique across the dimensions |
| `freight_rate_rules` | `rate_card_id`, `courier_id`, `weight_slab`, `zone` (nullable), `shipment_type`, `base_paise`, `cod_pct_bps`, `cod_min_paise`, `fuel_pct_bps` | Unique across the dimensions |
| `tax_rules` | `kind` (gst_tcs, income_tax_tds), `channel_id`, `rate_bps`, `effective_from`, `effective_to` | Used only to sanity-check reported tax |
| `settings` | `key`, `value` | Includes the ₹10 threshold |

### Ingestion

| Table | Key columns | Constraints and notes |
|---|---|---|
| `uploaded_files` | `partner_id`, `file_type`, `filename`, `sha256`, `size_bytes`, `uploaded_by`, `is_synthetic` | Unique `(partner_id, file_type, sha256)` catches a repeat upload |
| `uploaded_file_blobs` | `file_id`, `content_gzip` | Separate table, so listing files never loads their contents |
| `column_mappings` | `partner_id`, `file_type`, `active_version_id` | Unique `(partner_id, file_type)` |
| `mapping_versions` | `mapping_id`, `version`, `spec` (JSONB), `header_fingerprint`, `source` (ai, fallback, manual), `ai_suggestion` (JSONB), `approved_by`, `approved_at` | Immutable. Restoring an old version creates a new one that copies it, so history stays linear |
| `import_jobs` | `file_id`, `mapping_version_id`, `status`, `heartbeat_at`, row counts, timings, `error` | Also serves as the job queue. A stale heartbeat marks a job as interrupted |
| `import_row_errors` | `job_id`, `row_number`, `field`, `code`, `raw_value` | Downloadable as a report |

### Business records

| Table | Notes | Key constraint |
|---|---|---|
| `orders` | **Upserted.** A later order file legitimately updates delivery status | Unique `(channel_id, external_order_id)`; index on `tracking_id` |
| `settlement_lines` | Append-only, many per order | Unique `(partner_id, dedupe_key)` |
| `cod_remittances` | Append-only | Unique `(partner_id, dedupe_key)` |
| `freight_invoice_lines` | Append-only; forward and RTO lines can share an AWB | Unique `(partner_id, invoice_no, tracking_id, shipment_type)` |
| `return_records` | One or more per order | Unique `(partner_id, dedupe_key)` |
| `warehouse_receipts` | Matched to returns by AWB or order | Unique `(receipt_ref)` |

Every transaction table also has `order_id` (nullable FK, indexed), `match_method`, `source_file_id` and `source_row_number`. Those last two trace any figure back to the exact row of the original file, so raw rows aren't stored a second time.

`dedupe_key` is the partner's own reference when there is one, otherwise a hash of the canonical fields.

### Reconciliation and claims

| Table | Key columns | Notes |
|---|---|---|
| `reconciliation_runs` | `as_of_date`, `scope`, `status`, `triggered_by`, `rate_card_ids`, `settings_snapshot`, counts, timings | Records all inputs, so a result can be explained later |
| `reconciliation_results` | `run_id`, `order_id`, `primary_status`, `expected_paise`, `actual_paise`, `tcs_paise`, `tds_paise`, `explanation` (JSONB), `is_current` | **Deliberately stored calculated data:** it's the audit record of what was concluded and why. Partial unique index on `(order_id) WHERE is_current`. Only the latest few runs keep full rows, to stay inside the storage limit; run summaries and discrepancies are kept |
| `discrepancies` | `order_id`, `category`, `counterparty_partner_id`, `expected`, `actual`, `difference`, `due_date`, `state` (open, resolved, superseded), `first_seen_run_id`, `last_seen_run_id` | **Unique `(order_id, category, counterparty_partner_id)`**, so re-runs update it and never duplicate it |
| `match_suggestions` | `source_table`, `source_id`, `candidate_order_id`, `score`, `signals` (JSONB), `method`, `state`, `reviewed_by` | The human review queue. Never auto-confirmed |
| `claims` | `claim_no`, `discrepancy_id`, `partner_id`, `order_id`, `category`, `expected`, `actual`, `difference`, `recovered_amount`, `status`, `resolution_notes`, `created_by` | **Amounts are snapshotted on purpose:** what was filed mustn't change after later runs. One active claim per discrepancy |
| `claim_events` | `claim_id`, `from_status`, `to_status`, `amount`, `note`, `actor_id` | Timeline of the claim |
| `audit_logs` | `actor_id`, `action`, `entity_type`, `entity_id`, `before`, `after` | Append-only |

## 5. Ingestion pipeline

```
Upload → validate → hash → detect structure → suggest mapping → preview 20 rows
       → human approves → queued job → parse → validate → load → report
```

1. **File checks.** Allow-list of extension and actual content type, and a size limit set by `MAX_UPLOAD_MB` (default 25 MB). A repeated hash is blocked with a link to the earlier import.
2. **Structure detection.** Handles BOM and encoding, sheet choice, and preamble rows above the header. Everything is read as text, so leading zeros and long AWBs stay intact.
3. **Reuse a mapping.** An exact match on the header fingerprint offers the saved mapping. Any change in the headers forces a fresh review, so a changed partner format can't load silently.
4. **Mapping suggestion.** See section 6.
5. **Preview.** The first 20 rows run through the real validators, showing canonical values and row errors. Saving needs an explicit approval, and every required field must be mapped.
6. **Load.** Vectorised validation, then `COPY` into a temporary staging table, then a set-based `INSERT ... SELECT ... ON CONFLICT` in one transaction, over the direct database connection. A failed or interrupted job leaves nothing behind and can safely be run again.
7. **Report.** Rows inserted, updated, duplicate, rejected. Rejected rows are downloadable, and good rows still load, so one bad row doesn't block 99,999 others.

Parsing rejects what would quietly corrupt totals:

- Footer `TOTAL` rows, which would double-count
- AWBs Excel has turned into scientific notation, such as `1.23E+11`, which can't be recovered
- Dates that don't fit the mapping's declared format
- Mixed-format amounts are normalised (`₹1,499.00`, `(142.00)`), and anything still non-numeric is rejected

## 6. AI layer

```python
class MappingSuggester(Protocol):
    def suggest(self, ctx: MappingContext) -> MappingSuggestion: ...

# Implementations: HeuristicSuggester (synonyms + rapidfuzz), GeminiSuggester
```

The heuristic suggester is built first, because the upload workflow must work without any provider. The Gemini integration follows if time permits. **There is no simulated AI:** if the Gemini integration isn't finished, the product and documents describe mapping suggestions as rule-based only.

- **Output.** Structured JSON through Gemini's `response_schema`, validated again with Pydantic. Each column gets a canonical field or `null`, a confidence, a reason, and an ambiguity flag with alternatives.
- **Rejected output.** A field outside the canonical list, a column used twice for one field, or invalid JSON triggers the fallback. It is never partly applied.
- **Fallback.** Missing keys, timeouts, errors and rate limits switch to the heuristic matcher. The UI labels it "rule-based match" and doesn't show its score as AI confidence.
- **Low confidence.** Anything under 0.8, ambiguous or unsupported is left unselected, so it needs a deliberate choice.
- **Boundary.** Suggestions only ever fill a draft in the mapping form. Nothing in `services/recon` imports the AI package, and a test enforces that.
- **Privacy.** Only headers and a few sample values go to the provider. Values in columns that look like names, phones, emails or addresses are masked first.

Records with a missing order ID use **deterministic candidate scoring** on amount, date window, courier and pincode, and go to a review queue. Confirmation is always a human action. AI order-match suggestions are out of scope for this deadline. The brief allows them but doesn't require them.

## 7. Reconciliation engine

### Flow

```
1. Link     Attach unlinked records to orders: exact order ID → normalised ID → AWB → partner ref
            Multiple candidates → review queue, never picked automatically
2. Gather   One SQL aggregate per order, per stream, into a DataFrame of integer paise
3. Rate     Find the effective rule at the order date. No rule or a tie → Needs review
4. Evaluate Pure functions per stream. Each yields an outcome, amounts and explanation
5. Persist  One transaction: write results, flip is_current, upsert discrepancies
```

Runs use the direct database connection, under `REPEATABLE READ` with an advisory lock, so a concurrent import can't produce a half-consistent view and two runs can't overlap. The same data, rate card and `as_of_date` must give the same output, and a test enforces that.

### Three independent streams

An order can have more than one problem. A marketplace order can be short-paid **and** have an overcharged freight line, owed by different partners. So each order gets one result row with one primary status, and **zero or more discrepancies**, each with its own counterparty. Claims attach to discrepancies.

**A. Receivable** (settlement or COD)

```
expected   = rounded(base − pct × base − fixed)         delivered
expected   = −rounded(pct × base + fixed)               return event
actual     = Σ net_amount + Σ tcs + Σ tds               across all lines
difference = actual − expected
```

Adding reported tax back is what stops tax being counted as leakage. A settlement that withheld ₹6.91 in tax compares against the expected amount as if that tax had been paid, and the tax sits in its own columns.

| Condition | Outcome |
|---|---|
| No lines, payment not yet due | Pending |
| No lines, past `delivered_at + settlement_terms_days` | **Unpaid past due** |
| \|difference\| ≤ ₹10 | Settled, with the variance recorded |
| difference < −₹10 | **Short-paid** |
| difference > +₹10 | **Needs review** (overpaid). Not counted as money at risk |
| Channel normally withholds tax but the file has no tax columns | **Needs review.** Otherwise a tax deduction could look like a short payment |

**B. Freight** (per invoice line, against the freight rate rule)

| Condition | Outcome |
|---|---|
| billed − expected > ₹10 | **Freight overcharged**, stating the likely cause, such as billed slab 1 kg against declared 500 g |
| Billed for an AWB with no order | Review queue |
| Same AWB and shipment type on different invoices | **Needs review** as a possible duplicate charge |
| GST on freight | Compared separately from the charge, so a tax line never reads as an overcharge |

**C. Returns**

| Condition | Outcome |
|---|---|
| Return closed by courier, receipt found | Fine |
| No receipt after `returned_at + return_grace_days` | **Return not received** |
| Still inside the grace period | Pending |
| Quantity received is short, or condition is damaged | **Needs review** |

### Primary status

The fixed precedence is below. All discrepancies still show on the row, so this only picks the badge.

`Unpaid past due > Return not received > Short-paid > Freight overcharged > Needs review > Pending > Settled`

Pending is a confirmed **seventh status**, which the brief doesn't list. See A2.

### The explanation record

Every result stores what a reviewer needs to verify it:

```json
{
  "stream": "receivable",
  "rule": {"rate_card": "RC-2026-01 v3", "rule_id": 41, "pct_bps": 2500, "fixed_paise": 0},
  "calc": {"base": 119900, "deduction": 29975, "expected_raw": 89925, "expected": 89900},
  "actual": {"lines": [{"id": 8812, "net": 85509, "tcs": 571, "tds": 120, "file": 12, "row": 4471}], "total": 86200},
  "difference": -3700,
  "threshold": 1000,
  "reason": "Received ₹862.00 against expected ₹899.00: short by ₹37.00"
}
```

### Lifecycle across runs

- A discrepancy that still exists has its amount and `last_seen_run_id` updated.
- A discrepancy that no longer exists, such as when a late payment arrives, becomes `resolved`. If it has an open claim, the claim is flagged for human review and isn't closed automatically.
- **Money at risk** = Σ |difference| of open discrepancies, minus amounts already recovered on their claims.
- **Recovered** = Σ `recovered_amount` on claims, recorded by a person. A claim only counts as recovered when finance confirms it.
- **Auto-matched %** = orders settled with every link found deterministically, over orders evaluated, excluding Pending.

### Sample rows, as the engine would produce them

| Order | Rule (seed) | Expected | Actual | Difference | Status |
|---|---|---:|---:|---:|---|
| ORD-W-204118 | Website COD: 6% | 1,409 | 0 | −1,409 | Unpaid past due |
| ORD-M-88120 | Marketplace 1 Prepaid: 25% | 899 | 862 | −37 | Short-paid |
| ORD-W-204377 | Website RTO freight: flat ₹96 | −96 | −142 | −46 | Freight overcharged |

The rates are inferred from the sample and need confirming with the real client (A3). Treating ORD-W-204377 as a freight overcharge is confirmed for the demo (A4).

## 8. Claims

**Status flow:** `draft → submitted → acknowledged → partially_recovered → recovered`. `rejected` and `written_off` are the other terminal states. Each transition is checked on the server and logged in `claim_events`.

- Created from one discrepancy. Bulk creation and batch export are optional extras if time remains.
- **Evidence export** is an XLSX per claim: the calculation, the rate rule used, and every supporting record with its source file and row number.
- Exports neutralise spreadsheet formula injection in cells starting with `=`, `+`, `-` or `@`, because partner data ends up in a file someone will open in Excel.

## 9. API outline

All routes are under `/api/v1`. Lists are paginated with server-side filters. Long operations return `202` with a job ID.

| Area | Routes |
|---|---|
| Auth | `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` |
| Files | `POST /files`, `GET /files`, `GET /files/{id}`, `GET /files/{id}/download` |
| Mapping | `POST /files/{id}/mapping-suggestions`, `POST /files/{id}/preview`, `GET /mappings`, `GET /mappings/{id}/versions`, `POST /mappings/{id}/versions`, `POST /mappings/{id}/versions/{v}/restore` |
| Imports | `POST /imports`, `GET /imports`, `GET /imports/{id}`, `GET /imports/{id}/errors.csv` |
| Reconciliation | `POST /recon/runs`, `GET /recon/runs`, `GET /recon/runs/{id}`, `GET /recon/results`, `GET /recon/results/{order_id}`, `GET /recon/results/export.csv` |
| Review queue | `GET /match-suggestions`, `POST /match-suggestions/{id}/confirm`, `POST /match-suggestions/{id}/reject` |
| Claims | `POST /claims`, `GET /claims`, `GET /claims/{id}`, `PATCH /claims/{id}`, `POST /claims/{id}/transitions`, `GET /claims/{id}/evidence.xlsx` |
| Dashboard | `GET /dashboard/summary`, `/by-category`, `/by-partner`, `/aging` |
| Config | CRUD on `/partners`, `/rate-cards`, `POST /rate-cards/{id}/publish`, `/tax-rules`, `/settings` |
| Audit | `GET /audit-logs` |
| Health | `GET /healthz` (never touches the database, so it doesn't wake Neon) |

Errors use one shape: `{ "code", "message", "details" }`.

## 10. Frontend

```
/login
/                     Dashboard: KPIs, by category, by partner, aging
/imports              Upload, import history, row errors
/imports/[id]/mapping Mapping review and 20-row preview
/reconciliation       Order table: filters, run button, export
/reconciliation/[id]  Calculation, source records, timeline, create claim
/reconciliation/review  Match review queue
/claims               Claims list and bulk evidence export
/claims/[id]          Claim detail, status changes, notes, evidence
/settings/partners    Partners and payment terms
/settings/rate-cards  Rate cards: versions, rules, publish
```

Uses App Router, TanStack Query for server state and TanStack Table for server-side tables.

**Design direction:** closer to a ledger than a typical admin template. A neutral background, one accent, and red kept for money at risk so it stands out. Tabular numerals, right-aligned amounts, and dense but readable tables. No gradients, decorative icons or colourful KPI cards. Demo deployments carry a persistent "Synthetic demo data" banner.

**Server wake-up state:** the login page polls `/healthz` before enabling sign-in and says the server is starting, which can take about a minute. A slow first request therefore looks like a known state, not a broken product. API calls retry safe requests after a 502 or 504.

## 11. Repository layout

```
backend/
  app/api/          routers, schemas, auth dependencies
  app/core/         config, security, database, money helpers
  app/models/       SQLAlchemy models
  app/services/     ingestion/ matching/ rates/ recon/ claims/ dashboard/ ai/ jobs/
  alembic/  tests/  scripts/ (seed, generate data, benchmark)
frontend/
  src/app/  src/components/  src/lib/
docs/
docker-compose.yml
```

## 12. Security

- Secrets live in environment variables only, and `.env` is git-ignored.
- Login attempts are rate-limited.
- Uploaded files are never executed and never served inline.
- All SQL is parameterised, including dynamic filters.
- Sensitive actions are written to the audit log in the same transaction as the change.
- The repository only ever contains synthetic data. Demo credentials are demo-only and published in `deployment.md`.

## 13. Free-tier deployment

Total hosting budget is ₹0. Limits were checked on 5 November 2026 and should be rechecked at deployment.

| Service | Limit that matters | Design response |
|---|---|---|
| **Render Free** | Spins down after 15 idle minutes; waking takes about a minute | `/healthz` and the wake-up state above. Polling during a job counts as traffic |
| | Can be restarted at any time; filesystem is ephemeral | Nothing is kept on local disk beyond temporary processing. On startup, interrupted jobs are marked, and each job's single transaction means they can safely re-run |
| | No shell, one-off jobs or pre-deploy command | Migrations run in the start command (`alembic upgrade head`). Seeding runs from a developer machine against the direct Neon URL |
| | Reported as about 0.1 CPU and 512 MB RAM (third-party figure; not confirmed on Render's own pages) | One job at a time, compact dtypes, and measured deployed numbers. Chunked CSV parsing is the fallback if memory runs out |
| | 750 instance hours per month | Enough for one service, as long as the workspace runs nothing else |
| **Neon Free** | Scales to zero after 5 idle minutes; can't be disabled; Neon says it resumes in hundreds of milliseconds | `pool_pre_ping`, connection recycling and a retry when connecting. Actual resume time gets measured |
| | 1 GB storage per project | Compressed uploads, retention on old result rows, and a demo seed of about 3,000 orders. Writes fail if this is exceeded |
| | 100 CU-hours and 5 GB public transfer per project per month. Going over suspends the database | No keep-warm pings on the database. Autoscaling capped low. A **separate Neon project** for deployed benchmarks, so they can't exhaust or pollute the demo |
| | The pooled connection doesn't support session advisory locks or temp tables that outlive a commit | Two URLs: `DATABASE_URL` (pooled) for API requests, `DATABASE_URL_DIRECT` for jobs and migrations |
| **Vercel Hobby** | Personal, non-commercial use only | Fine for this assignment. A real client deployment would need a paid plan |
| | Proxy timeout (30 or 120 seconds, see section 1); possible body limit | Asynchronous jobs, and the upload test in Milestone 1 |
| **Gemini free tier** (if used) | May use inputs for training, according to secondary sources | Synthetic data only. See A22 |

**Performance reporting:** the 100k benchmark is run locally and reported as the local result. Deployed performance is measured separately and reported as measured, even if it misses the target or fails. No document may claim the free infrastructure meets the target unless a measurement shows it.
