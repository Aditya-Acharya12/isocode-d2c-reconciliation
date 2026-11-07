# Architecture

Status: approved, with the foundation built (Milestone 1). Later sections describe parts still to come, and [implementation-plan.md](implementation-plan.md) tracks progress. Hosting is limited to free tiers, and the deadline is 9 November 2026, 11:59 PM IST. Assumption IDs such as A3 refer to [assumptions.md](assumptions.md).

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
| One in-process job thread, running one job at a time (section 14) | Simplest option that can be tested by the deadline. A second process would load pandas again on a small free instance | A heavy job can slow API responses, and jobs can't run in parallel |
| Pandas for parsing, `COPY` for loading, SQL for aggregation | The only realistic way to reach 100k rows in under a minute | Less ORM use in the bulk paths |
| Engine calculates in integer paise; database stores `NUMERIC(14,2)` | Floats must never touch money | Conversion is needed at the boundary |
| Uploaded files gzip-compressed in a `bytea` table | Render's filesystem is wiped on restart, and evidence must survive | Uses Neon's 1 GB allowance, so uploads have a size limit |
| Pure-function rule evaluation | Financial rules can be tested without a database | Requires discipline about layer boundaries |

### Authentication

Passwords are hashed with Argon2. Sessions are JWTs in an httpOnly, SameSite=Lax cookie, lasting 8 hours by default (`ACCESS_TOKEN_MINUTES`). Next.js rewrites `/api/*` to the backend, so the browser sees one origin and third-party cookie blocking doesn't apply.

The user is read from the database on every request, and tokens carry a `token_version`. So signing out revokes the token, and a deactivation or role change applies immediately. This costs one primary-key lookup per request.

There's no Next.js route guard (`proxy.ts`). The API is the only access control, and a 401 sends the browser to the login page.

Vercel's documentation pages show proxied request timeouts of both 30 and 120 seconds, so the design assumes 30. Every long-running operation returns a job ID and the frontend polls for it. Vercel documents a 4.5 MB body limit for Functions, but I couldn't confirm whether it also applies to external rewrites. **`backend/scripts/check_upload_limit.py` measures this on the deployment.** If the limit applies, uploads go directly to the API using a short-lived, upload-only token.

`next start` buffers proxied request bodies up to `proxyClientMaxBodySize`, and uploads over that limit failed in local testing. It's set to 30 MB, above the API's limit. Next's documentation also warns that bodies over this limit can be silently truncated, so in Milestone 2 the browser will send a SHA-256 of each file, and the API will reject any upload that doesn't match.

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
| `uploaded_files` | `partner_id` or `channel_id`, `file_type`, `filename`, `sha256`, `size_bytes`, `uploaded_by`, `is_synthetic`, `detection` | Comes from exactly one source: orders from a sales channel, everything else from a partner. The same content can't be uploaded twice for a file type, even under a different source, because that would count the same money twice |
| `uploaded_file_blobs` | `file_id`, `content_gzip` | Separate table, so listing files never loads their contents |
| `column_mappings` | `partner_id` or `channel_id`, `file_type`, `active_version_id` | One per source and file type |
| `mapping_versions` | `mapping_id`, `version`, `spec` (JSONB), `header_fingerprint`, `source` (ai, fallback, manual), `ai_suggestion` (JSONB), `approved_by`, `approved_at` | Immutable. Restoring an old version creates a new one that copies it, so history stays linear |
| `background_jobs` | `kind` (file_analysis, validation, import, reconciliation), `status`, `payload`, `result`, `idempotency_key`, `attempts`, `max_attempts`, `locked_by`, `heartbeat_at`, `error` | The persisted queue. Unique `idempotency_key` stops the same work being queued twice |
| `import_jobs` | `file_id`, `mapping_version_id`, `background_job_id`, row counts, timings | The business record of an import |
| `import_row_errors` | `import_job_id`, `row_number`, `field`, `code`, `severity` (error, warning, duplicate), `message`, `raw_value` | Every issue found in a row, downloadable as a report. Stored up to 20,000 per import, worst first, to protect Neon's free storage. Counts always stay exact |

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
| `reconciliation_results` | `run_id`, `order_id`, `primary_status`, `expected_paise`, `actual_paise`, `tcs_paise`, `tds_paise`, `explanation` (JSONB), `is_current` | **Deliberately stored calculated data:** it's the audit record of what was concluded and why. Partial unique index on `(order_id) WHERE is_current`. Only the current run keeps full rows, to stay inside the storage limit. Run summaries and discrepancies are kept |
| `discrepancies` | `order_id`, `category`, `counterparty_partner_id`, `expected`, `actual`, `difference`, `due_date`, `state` (open, resolved, superseded), `first_seen_run_id`, `last_seen_run_id` | **Unique `(order_id, category, counterparty_partner_id)`**, so re-runs update it and never duplicate it |
| `match_suggestions` | `source_table`, `source_id`, `candidate_order_id`, `score`, `signals` (JSONB), `method`, `state`, `reviewed_by` | The human review queue. Never auto-confirmed |
| `claims` | `claim_no`, `discrepancy_id`, `partner_id`, `order_id`, `category`, `expected`, `actual`, `difference`, `recovered_amount`, `status`, `resolution_notes`, `evidence` (JSONB, migration 0003), `created_by` | **Amounts and evidence are snapshotted on purpose:** what was filed mustn't change after later runs. One active claim per discrepancy |
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
1. Link     Fill empty order links in three set-based passes: exact order ID → normalised
            order ID → tracking ID (AWB). A record links only if it matches exactly one
            order, and a partner tied to a channel only matches that channel's orders.
            Existing links are never changed. Anything else stays unlinked and is listed.
2. Gather   Load orders and linked records, with amounts converted to integer paise in SQL
3. Rate     Find the published rule in force on the order date. No rule or a tie → Needs review
4. Evaluate Pure functions per stream (services/reconciliation/engine.py, no database access)
5. Persist  Same transaction: clear is_current, COPY the new results, upsert discrepancies,
            resolve the ones that disappeared, record the run's inputs and the audit entry
```

A run is a queued background job (`JobKind.RECONCILIATION`) that runs in **one transaction under `pg_advisory_xact_lock`**, so two runs can't interleave and a failed run leaves nothing behind. Only one run can be queued or running at a time. The same data, rate cards, threshold and `as_of_date` give the same output, and a test re-runs to prove it. Orders are evaluated 5,000 at a time, so memory stays flat. Full result rows are kept for the current run only, because each run at 100,000 orders takes about 175 MB (see [benchmarks.md](benchmarks.md)). Run summaries, discrepancies, claim evidence and the audit log keep the history.

**The AI layer is never consulted.** The engine imports nothing from ingestion's suggestion code, and every figure comes from rate cards and imported records.

A record that links to no order is listed under "Records not linked to an order" with the reason. The fuzzy candidate scoring and review queue described in section 6 weren't built for this deadline.

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
| Some lines, payment not yet due, still short | Pending (partly paid) |
| \|difference\| ≤ ₹10 | Settled, with the variance recorded |
| difference < −₹10 | **Short-paid** |
| difference > +₹10 | **Needs review** (overpaid). Not counted as money at risk |
| Channel normally withholds tax, none is reported, and the order looks short | **Needs review.** Otherwise a tax deduction could look like a short payment |
| Money arrived by the wrong route (a settlement for a COD order) or from an unexpected payer | **Needs review** |
| No rule, no delivery date, or no courier on a COD order | **Needs review** |
| Return charge expected but not billed yet | Pending |

The payer comes from the rule. A rule with no payer means the order's courier, paid through COD remittances. Otherwise the named partner pays through settlements. The due date is `delivered_at` plus that payer's `settlement_terms_days`.

**B. Freight** (per invoice line, against the freight rate rule)

| Condition | Outcome |
|---|---|
| billed − expected > ₹10 | **Freight overcharged**, stating the likely cause, such as billed slab 1 kg against declared 500 g |
| Billed for an AWB with no order | Listed as an unlinked record, outside every total |
| Two lines of the same shipment type for one order | **Needs review** as a possible duplicate charge |
| No freight rule for the courier, slab and zone | **Needs review** |
| GST on freight | Excluded from the billed amount, so a tax line never reads as an overcharge |

Expected freight = base rate + COD charge (`max(cod_pct × order value, cod_min)`, forward COD shipments only) + fuel surcharge. An exact-zone rule beats an any-zone rule.

**C. Returns**

| Condition | Outcome |
|---|---|
| Warehouse receipt found | Settled. A courier marking it returned isn't enough |
| No receipt after `returned_at + return_grace_days` | **Return not received**, valued at the order value as stock at risk |
| Still inside the grace period | Pending |
| Quantity received is short, or condition is damaged | **Needs review** |
| Returned with no return date | **Needs review** |

The grace period is the return partner's, or the courier's when there's no return record.

### Primary status

The fixed precedence is below. All discrepancies still show on the row, so this only picks the badge.

`Unpaid past due > Return not received > Short-paid > Freight overcharged > Needs review > Pending > Settled`

Pending is a confirmed **seventh status**, which the brief doesn't list. See A2.

### The explanation record

Every result stores what a reviewer needs to verify it:

```json
{
  "primary_stream": "receivable",
  "reason": "Received ₹862.00 against ₹899.00 expected: short by ₹37.00.",
  "precedence": ["unpaid_past_due", "return_not_received", "short_paid", "..."],
  "streams": {
    "receivable": {
      "status": "short_paid",
      "rule": {"rate_card": "Demo rate card v1", "rule_id": 41, "pct_bps": 2500, "fixed_paise": 0},
      "base_paise": 119900, "deduction_paise": 29975, "expected_paise": 89900,
      "net_received_paise": 85509, "tcs_paise": 571, "tds_paise": 120, "actual_paise": 86200,
      "difference_paise": -3700, "threshold_paise": 1000, "due_date": "2026-10-21",
      "lines": [{"kind": "settlement", "id": 8812, "file_id": 12, "row": 2, "...": "..."}]
    },
    "freight": {"status": "settled", "lines": []},
    "returns": null
  }
}
```

Each stream's record lists the source lines with their file and row, which is what the order page and claim evidence show.

### Lifecycle across runs

- A discrepancy that still exists has its amount and `last_seen_run_id` updated.
- A discrepancy that no longer exists, such as when a late payment arrives, becomes `resolved`. If it has an open claim, the claim page warns about it, and the claim isn't closed automatically. A discrepancy that comes back is reopened.
- **Money at risk** = the part of open cash discrepancies (unpaid, short-paid, freight overcharged) still at risk once their claims are counted. Section 8 gives the exact rule.
- **Stock at risk** = the same, for open "return not received" discrepancies, valued at order value. It's shown separately, because it's goods, not cash.
- **Written off** = what closed claims stopped chasing. It's reported separately, never inside money at risk.
- **Recovered so far** = Σ `recovered_amount` on the claims of open cash discrepancies, recorded by a person. A claim only counts as recovered when finance confirms it.
- **Reconciled** = orders whose primary status is Settled.

### Sample rows, as the engine produces them (tested)

| Order | Rule (seed) | Expected | Actual | Difference | Status |
|---|---|---:|---:|---:|---|
| ORD-W-204118 | Website COD: 6% | 1,409 | 0 | −1,409 | Unpaid past due |
| ORD-M-88120 | Marketplace 1 Prepaid: 25% | 899 | 862 | −37 | Short-paid |
| ORD-W-204377 | Website RTO freight: flat ₹96 | −96 | −142 | −46 | Freight overcharged |

The rates are inferred from the sample and need confirming with the real client (A3). Treating ORD-W-204377 as a freight overcharge is confirmed for the demo (A4).

## 8. Claims

**Status flow:** `draft → submitted → acknowledged → partially_recovered → recovered`. `rejected` and `written_off` are the other terminal states. Each transition is checked on the server (`services/reconciliation/claims.py`), logged in `claim_events` and audited.

- **One claim per discrepancy**, created in bulk from the "Ready to claim" list, which groups open cash and return discrepancies by the partner who owes them. A resolved discrepancy can't be claimed, and nor can one whose claim is in progress (draft to partly recovered) or recovered. Only a rejected or written-off claim frees the discrepancy for a fresh claim. This is the same rule as the partial unique index on `claims.discrepancy_id`, so the list never offers something the database would refuse. A batch with any ineligible discrepancy creates nothing and names the ones refused.
- **Evidence is snapshotted** into `claims.evidence` (JSONB, migration 0003) when the claim is created: the order, the stream's calculation and every supporting record with its source file and row. Later runs can't change what was filed.
- Recovered amounts are running totals that can't decrease or exceed the claimed amount. Recovered defaults to the full amount. Rejected and written-off need a note.
- **Freight evidence holds only the claimed courier's invoice lines.** An order can be billed by two couriers, such as a forward leg by one and the RTO by another, and one courier mustn't see the other's invoice numbers or amounts. The stream's totals are replaced by the discrepancy's own figures for the same reason. The order page still shows every line.

**What's still at risk on an open discrepancy** (`split_exposure` in `services/reconciliation/claims.py`). The governing claim is the one blocking a new claim if there is one, otherwise the latest written-off one. A rejected claim never governs. With:

- *current* = |difference| as the latest run sees it
- *claimed* = |difference| when the claim was filed
- *recovered* = the running total finance entered

then:

| | Formula |
|---|---|
| Recovery the data already shows | *in data* = max(0, *claimed* − *current*) |
| Recovery entered but not yet in the data | *not in data* = max(0, *recovered* − *in data*) |
| Outstanding | max(0, *current* − *not in data*) |

| Governing claim | At risk | Written off |
|---|---|---|
| None, or rejected | *current* | 0 |
| Draft, submitted, acknowledged, partly recovered | outstanding | 0 |
| Recovered or written off | outstanding − written off | min(outstanding, *claimed* − *recovered*) |

What this means in practice:

- **A recovery is counted once.** If a recovery reaches the data, as a later settlement or remittance, the run already shows a smaller shortfall, and only the part the data doesn't yet show is subtracted. For example, a ₹37 claim with ₹20 entered leaves ₹17 at risk, and it's still ₹17 after a ₹20 payment is imported, not ₹−3.
- **A fully recovered claim never puts money back at risk.** The discrepancy stays open while the data doesn't show the payment, but contributes nothing.
- **A claim closed as recovered for less than it claimed** counts the rest as written off, since nothing more is being chased.
- **Written off** never counts as money at risk, and is reported separately. Claiming the discrepancy again puts it back at risk.
- **A shortfall that grows beyond a closed claim** stays at risk for the growth only.
- No figure is ever negative, and at risk plus written off never exceeds what the run still sees.

The summary also returns, per category, the gross shortfall (`amount`), `at_risk` and `written_off`. Stock at risk follows the same rule.
- **Evidence export** is CSV, one file per claim or one per partner, with an order row and a row per supporting record. It neutralises spreadsheet formula injection in text cells starting with `=`, `+`, `-`, `@`, tab or carriage return, because partner data ends up in a file someone will open in Excel. Numbers are left as numbers. XLSX was planned but CSV opens in the same tools and was enough for the deadline.

## 9. API outline

All routes are under `/api/v1`. Lists are paginated with server-side filters. Long operations return `202` with a job ID.

| Area | Routes |
|---|---|
| Auth | `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` |
| Reference | `GET /ingestion/schema`, `GET /sales-channels` |
| Files | `POST /files`, `GET /files`, `GET /files/{id}`, `GET /files/{id}/download`, `DELETE /files/{id}` (only before import) |
| Mapping | `POST /files/{id}/mapping-suggestions`, `POST /files/{id}/validations`, `GET /mappings`, `GET /mappings/{id}/versions`, `POST /mappings/{id}/versions/{v}/restore` |
| Imports | `POST /files/{id}/imports`, `GET /imports/{id}`, `POST /imports/{id}/retry`, `GET /imports/{id}/errors`, `GET /imports/{id}/errors.csv`. Import history is `GET /files` |
| Jobs | `GET /jobs/{id}` |
| Reconciliation | `POST /reconciliation/runs` (finance), `GET /reconciliation/runs`, `GET /reconciliation/summary`, `GET /reconciliation/results` (status, channel, partner, category, date range, search, sort), `GET /reconciliation/orders/{order_id}`, `GET /reconciliation/returns`, `GET /reconciliation/unmatched`, `GET /reconciliation/tax` |
| Claims | `GET /claims/eligible`, `POST /claims` (finance), `GET /claims`, `GET /claims/evidence.csv?partner_id=`, `GET /claims/{id}`, `POST /claims/{id}/transitions` (finance), `GET /claims/{id}/evidence.csv` |
| Config | `/partners` (co-founder writes), `GET /rate-cards` (read-only for now) |
| Not built | Match review queue, dashboard breakdowns, rate-card editing, results export |
| Audit | `GET /audit-logs` |
| Health | `GET /api/healthz` (never touches the database, so it doesn't wake Neon) |

Errors use one shape: `{ "code", "message", "details" }`.

## 10. Frontend

```
/login
/                         Overview
/imports                  Upload and import history
/imports/[id]             Mapping review, preview, results and row errors for one file
/reconciliation           Summary, run button, filterable order table, unlinked records, tax withheld
/reconciliation/[orderId] How the status and amounts were calculated, per stream, with source records
/returns                  Returns by state: awaiting scan, not received, received, needs review
/claims                   Ready to claim (by partner), claims list, partner evidence CSV
/claims/[id]              Claim detail, status changes, evidence as filed, history
/settings/mappings        Saved column mappings and their versions
/settings/rate-cards      Rate cards in force (read-only)
/settings/partners        Partners and payment terms
/activity                 Audit log (co-founder) or own activity (finance)
```

Uses App Router, TanStack Query for server state and TanStack Table for server-side tables.

**Design direction:** closer to a ledger than a typical admin template. A neutral background, one accent, and red kept for money at risk so it stands out. Tabular numerals, right-aligned amounts, and dense but readable tables. No gradients, decorative icons or colourful KPI cards. Demo deployments carry a persistent "Synthetic demo data" banner.

**Server wake-up state:** the login page polls `/api/healthz` before enabling sign-in and says the server is starting, which can take about a minute. A slow first request therefore looks like a known state, not a broken product. API calls retry safe requests after a 502 or 504.

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
- Deployment credentials stay outside the repository. `scripts/with_env.py` loads them for a single local command, and masks them in its output.
- The test suite drops the whole schema, so it refuses to run on any database not named for testing.

## 13. Free-tier deployment

Total hosting budget is ₹0. Limits were checked on 5 November 2026 and should be rechecked at deployment.

| Service | Limit that matters | Design response |
|---|---|---|
| **Render Free** | Spins down after 15 idle minutes; waking takes about a minute | `/healthz` and the wake-up state above. Polling during a job counts as traffic |
| | Can be restarted at any time; filesystem is ephemeral | Nothing is kept on local disk beyond temporary processing. On startup, interrupted jobs are marked, and each job's single transaction means they can safely re-run |
| | No shell, one-off jobs or pre-deploy command | `scripts/start.py` runs migrations, then starts the server. A Postgres advisory lock makes concurrent starts wait for each other, and it's released automatically if the holder crashes. Seeding runs from a developer machine against the direct Neon URL |
| | Reported as about 0.1 CPU and 512 MB RAM (third-party figure; not confirmed on Render's own pages) | One job at a time, compact dtypes, and measured deployed numbers. Chunked CSV parsing is the fallback if memory runs out |
| | 750 instance hours per month | Enough for one service, as long as the workspace runs nothing else |
| **Neon Free** | Scales to zero after 5 idle minutes; can't be disabled; Neon says it resumes in hundreds of milliseconds | `pool_pre_ping`, connection recycling and a retry when connecting. Actual resume time gets measured |
| | 1 GB storage per project | Compressed uploads, retention on old result rows, and a demo seed of about 3,000 orders. Writes fail if this is exceeded |
| | 100 CU-hours and 5 GB public transfer per project per month. Going over suspends the database | No keep-warm pings on the database, and autoscaling capped low. If a deployed benchmark is run, it should use a separate Neon project, so it can't exhaust or pollute the demo |
| | The pooled connection doesn't support session advisory locks or temp tables that outlive a commit | Two URLs: `DATABASE_URL` (pooled) for API requests, `DATABASE_URL_DIRECT` for jobs and migrations |
| **Vercel Hobby** | Personal, non-commercial use only | Fine for this assignment. A real client deployment would need a paid plan |
| | Proxy timeout (30 or 120 seconds, see section 1); possible body limit | Asynchronous jobs, and the upload test in Milestone 1 |
| **Gemini free tier** (if used) | May use inputs for training, according to secondary sources | Synthetic data only. See A22 |

**Performance reporting:** the reproducible 100k benchmark is run locally and reported as the local result. Deployed performance is reported separately if measured, even if it misses the target or fails, and documents say so plainly if it wasn't measured. No document may claim the free infrastructure meets the target unless a measurement shows it.

## 14. Background job guarantees

Analysis, validation and import all run as jobs, in `app/services/jobs.py`. Full-file work can't run inside a request, because a proxied request may be cut off after 30 seconds. The worker thread sleeps until a job arrives and doesn't poll the database while idle, as a timer would stop Neon scaling to zero and use up its monthly compute. The one exception is a job marked running by another instance, which is rechecked every 60 seconds until it finishes or goes stale. Pandas is only imported when the first job runs, so an idle server doesn't hold it in memory.

Tests kill a real process just before commit, and confirm that nothing was written and that the job recovers and runs exactly once.

| Guarantee | How |
|---|---|
| **Persisted** | Every job is a row, written before any work starts |
| **Recoverable** | The thread updates `heartbeat_at` while working. At startup, and periodically, a running job with a stale heartbeat is returned to the queue, up to `max_attempts`, then marked failed with a visible reason |
| **Idempotent** | A job's business effects commit in one transaction, along with its status. An interrupted job therefore left nothing behind, and row-level unique constraints stop a re-run from duplicating data |
| **Single execution** | Jobs are claimed with `FOR UPDATE SKIP LOCKED`, so an old and a new instance overlapping during a deploy can't both take one |
