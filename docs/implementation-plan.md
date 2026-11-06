# Implementation plan

**Deadline:** 9 November 2026, 11:59 PM IST. **Target submission:** 8:00 PM IST that day, leaving about four hours for anything unexpected.

Planning finished on the evening of 5 November, which leaves about four days and one developer. This replaces the original seven-day roadmap.

## The goal

One workflow matters more than anything else. It must work end to end, on the deployed site, with real data flowing through it:

**Upload → Mapping approval → Import → Reconciliation → Discrepancy review → Claim creation → Evidence export**

Everything is prioritised by whether this workflow needs it.

## Priorities

| Tier | Meaning | Contents |
|---|---|---|
| **P0** | The submission fails without it | The workflow above, including settlement, COD, freight and returns logic. Login and RBAC, saved and versioned mappings, the 20-row preview, heuristic suggestions, rate-card calculations, tax separation, explanations, duplicate detection, the dashboard, the local 100k benchmark, deployment, and the submission documents |
| **P1** | Expected, and built if P0 is on track | Gemini integration, rate-card editing in the UI, the missing-order-ID review queue screen, mapping restore in the UI, deployed benchmark on a separate database (which must not delay implementation) |
| **P2** | Only with time left over | Bulk claims, batch evidence export, audit log screen, CI, mobile refinement of dense tables |

Rate cards and unmatched records still work at P0. Rate cards can be viewed in the UI and seeded, and records with no matching order appear under Needs review, even if the dedicated queue screen slips.

**Never cut:** approval before a mapping is saved, tax separation, discrepancy explanations, backend role enforcement, and honest benchmark reporting.

## Milestones

Each milestone ends with a running app and a working screen. The UI grows alongside the backend, so integration problems appear early.

### Milestone 1: Foundation (finish by end of 6 November)

**Status: built and tested locally, and the Neon database is verified. Deployed to Render with Neon, as reported by the project owner.**

**Build**
- Project structure, `main` branch, public repository, Docker Compose for local Postgres
- Full database schema with Alembic migrations
- Login with Argon2 and JWT cookies, plus RBAC
- Next.js app shell: navigation, protected routes, role-aware menu, synthetic data banner, server wake-up state
- Deployment to Vercel, Render and Neon

**Test**
- Each role is refused the other's actions on the backend
- A 20 MB upload through the Vercel rewrite, which settles the upload route
- Render cold start and Neon resume times, measured

**Done when:** both demo users can log in on the deployed URL and see the correct navigation for their role.

### Milestone 2: Ingestion (finish by end of 7 November)

**Status: built and tested locally, awaiting review before deployment. See [benchmarks.md](benchmarks.md) for measured performance.**

**Build**
- CSV and XLSX upload, file checks, compressed storage, duplicate detection
- Structure detection and row validators
- Heuristic mapping suggestions, then Gemini (P1) with its failure handling
- 20-row preview, explicit approval, mapping versions and header fingerprints
- Import jobs with the background thread, `COPY` loading and row error reports, with the persistence, recovery and idempotency guarantees in architecture section 14
- A browser-computed SHA-256 for each upload, checked by the API, so a truncated file is rejected
- **Screens:** upload, import history with live job status, mapping review and preview
- Data generator and benchmark script

**Test**
- Invalid, oversized, duplicate and malformed files, including footer rows and corrupted AWBs
- Rollback on a failed job
- **Local 100k benchmark**, CSV and XLSX

**Done when:** a partner file with unfamiliar headers can be uploaded, mapped, approved and imported through the deployed UI, and the local 100k time has been measured.

**Checkpoint:** if the local benchmark is over 60 seconds, fix that before starting Milestone 3, as it may affect the design.

### Milestone 3: Business engine (finish by end of 8 November)

**Build**
- Rate cards with effective dates and rule lookup
- Deterministic record linking
- Settlement and COD matching, freight discrepancies, returns tracking, tax add-back
- Reconciliation runs, explanations and discrepancy lifecycle
- Claims with status changes and XLSX evidence export
- Seed script that loads differently formatted partner files through the real pipeline
- **Screens (functional, polish comes later):** reconciliation table, order detail with calculation, claim creation, claim list and detail with evidence download

**Test**
- Financial rules, including ₹10.00 against ₹10.01, tax add-back and multi-line settlements
- **All three sample orders produce the brief's exact figures**
- Re-running creates no duplicates and gives identical results
- Local 100k reconciliation benchmark

**Done when:** the complete workflow runs from upload to evidence export in the browser. **This is the most important checkpoint in the plan.**

### Milestone 4: Product completion (9 November, freeze at 8:00 PM IST)

**Build**
- Dashboard from database queries, with partner and category breakdowns and aging
- Partner and rate-card configuration screen
- Final design pass: every button works, and every screen has loading, empty and error states

**Test**
- End-to-end check of both roles on the deployed site
- Dashboard totals compared with independently calculated values
- Security review: access control, uploads, secrets, CSV and formula injection
- Secret scan of the Git history
- Fresh-clone setup test from the README
- Deployed benchmark, reported as measured

**Deliver**
- Final README
- Technical Design Document (PDF or Word) with the GitHub URL at the top
- `deployment.md`
- Submission ZIP containing exactly those two files

**Risk:** this milestone is the heaviest. Writing the design document starts during Milestone 3, using these docs, so it isn't left until the last evening. If Milestone 3 slips, trim polish and P1 work first. The dashboard and the documents stay.

## If time runs short

Cut in this order:

1. Remaining P2 items
2. New Playwright coverage (a written manual end-to-end checklist remains)
3. Gemini integration (heuristics remain, and documents say so)
4. Mapping restore in the UI (the API remains)
5. Rate-card editing in the UI (viewing and seeded data remain, and no button is shown that doesn't work)
6. Review queue screen (unmatched records stay visible under Needs review)
7. Deployed benchmark (documents state it wasn't measured)

## Performance plan

The target is 100,000 rows in under 60 seconds. These are **estimates to be replaced by measurements**, not results.

| Step | Approach | Local estimate |
|---|---|---:|
| Parse CSV | pandas with the pyarrow engine | 1 s |
| Parse XLSX | pandas with the calamine engine, as openpyxl is much slower | 5–10 s |
| Validate | Vectorised, no per-row Python | 2–3 s |
| Load | `COPY` into staging, then set-based insert | 5–10 s |
| Reconcile 100k orders | SQL aggregates, vectorised rules, `COPY` results | 15–30 s |

The likely bottleneck is generating JSON explanations for each row, so those only get built in full for orders with something to explain.

| Measurement | Environment | Reporting rule |
|---|---|---|
| Target benchmark | Local machine and local Postgres | Reported with machine specs and per-step timings |
| Deployed benchmark (if time permits) | Render Free with a separate Neon project | Reported separately, as measured, including a miss or out-of-memory failure. If it isn't run, documents say so |

A local pass says nothing about the free hosting. No document may imply it does.

## Testing

| Layer | Focus | Tier |
|---|---|---|
| Unit | Money rounding, rate lookup, the ₹10 boundary, tax add-back, each status, precedence, multi-line settlements | P0 |
| Integration (real Postgres) | Duplicate imports, idempotent re-runs, rollback, claims across re-runs, role enforcement | P0 |
| File handling | Wrong formats, oversized files, encodings, footer rows, corrupted AWBs, formula injection | P0 |
| Heuristic mapping | Known headers, ambiguous headers, unsupported columns | P0 |
| Gemini | Invalid JSON, unknown fields, timeouts, low confidence, and proof that suggestions can't reach calculations | P1, with the integration |
| Frontend | Playwright tests of the main workflows. These started in Milestone 1, and can also run against a deployment | P0 |

## Risks

| Risk | Mitigation |
|---|---|
| Milestone 4 overloads the final day | Screens are built from Milestone 2 onward, the design document starts in Milestone 3, and there's a four-hour buffer |
| 100k rows run out of memory on Render Free | Measured on a separate database in Milestone 4 or sooner. Chunked CSV parsing is the fallback. A failure is reported, not hidden |
| Cold start makes the demo look broken | Wake-up state on the login page. `deployment.md` warns reviewers to expect it |
| Neon limits reached before review | Small demo seed, no keep-warm pings on the database, benchmarks in a separate project |
| Vercel upload limits apply to rewrites | Tested in Milestone 1. Direct upload with a short-lived token is the fallback |
| Client's real files differ from the synthetic ones | Mapping handles format differences; A13 is flagged as the largest unknown |
| Rate-card design doesn't fit real contracts | Percent, fixed and slab rules cover the common cases. Anything else goes to Needs review, not a guess |
| Secrets or real data reach the public repo | `.gitignore` from the first commit, synthetic data only, and a history scan before submission |
