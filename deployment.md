# Deployment and demo guide

D2C Order, Payment and Returns Reconciliation Platform, built for the Isocode take-home assignment.

| | |
|---|---|
| Live app | https://isocode-d2c-reconciliation.vercel.app |
| API health | https://isocode-d2c-reconciliation-api.onrender.com/api/healthz |
| Source | https://github.com/Aditya-Acharya12/isocode-d2c-reconciliation |
| Hosting | Vercel Hobby (frontend), Render Free (API), Neon Free (PostgreSQL 16, AWS Singapore). Total cost ₹0 |

All data in the deployment and the repository is synthetic.

## 1. Reviewer access

### Demo accounts

| Role | Email | Password |
|---|---|---|
| Finance Executive | `finance@loomandfold.example` | Provided with the submission |
| Co-founder | `founder@loomandfold.example` | Provided with the submission |

The passwords are the existing demo credentials, generated at deployment time. They're provided in the submitted copy of this document and are never committed to the repository.

**The first request can take about a minute.** Render's free instance sleeps after 15 idle minutes. The login page says the server is starting, and enables sign-in once it answers. Neon also suspends after 5 idle minutes and resumes on the next query.

### What the deployment holds today

- **150 synthetic orders** (`ISO-TEST-0001` to `ISO-TEST-0150`), all on the Website channel, dated 1 to 25 October 2026, imported through the app from `iso-test-orders.csv`.
- **No partner files yet.** There are no settlement, COD remittance, freight invoice, return or warehouse receipt records.
- The published **Demo rate card v1**, with 24 settlement rules, 16 freight rules and 10 tax reference rules.
- **One reconciliation run** (run 1, as of 8 November 2026). It completed in about 3.8 seconds and evaluated all 150 orders:

| Status | Orders | Why |
|---|---:|---|
| Unpaid past due | 109 | Delivered and past the payer's terms, with nothing received, because no settlement or remittance files are loaded |
| Pending | 28 | 25 still in transit, and 3 COD orders due on the day of the run |
| Needs review | 13 | Returned to origin (RTO) but the order file has no return date, so the return grace period can't be applied |

Money at risk is ₹2,72,640.00 across 109 open discrepancies: 54 owed by the Payment Gateway (prepaid) and 55 by the two courier aggregators (COD). **This reflects missing partner data, not proven leakage.** Partner matching hasn't been demonstrated on the deployment yet, because no partner files are loaded there. It is covered by the automated tests, including the browser test that imports the three sample cases and reconciles them.

No claims have been created.

## 2. Demo walkthrough

### Roles

| Capability | Finance Executive | Co-founder |
|---|---|---|
| View overview, imports, reconciliation, returns, claims, rate cards | Yes | Yes |
| Upload files, approve mappings, import | Yes | No |
| Run reconciliation | Yes | No |
| Create claims and change their status | Yes | No |
| Edit partners and payment terms | No (read only) | Yes |
| Audit log | Own actions | Everyone's |

Roles are enforced by the API. Hidden buttons are only a convenience, and a direct API call without the permission returns 403.

### Suggested path (Finance Executive)

1. **Sign in** as the Finance Executive. The "Synthetic demo data" banner is shown throughout.
2. **Reconciliation.** The summary shows orders evaluated, money at risk, written off, stock at risk and each status. The table filters by status, channel, partner and date, and searches by order ID or tracking ID.
3. **Open an order**, for example `ISO-TEST-0001`. The page explains the status per stream: the rule and rate card used, base amount, deduction, expected amount, amount received, difference, due date and the ₹10 threshold.
4. **Returns.** Returns grouped by state. The 13 RTO orders appear under Needs review, with the reason.
5. **Claims.** "Ready to claim" groups open discrepancies by the partner who owes them. Creating claims writes to the demo database, so leave this step for last if you want the data unchanged.
6. **Imports.** Upload a small synthetic file to see the workflow: structure detection, suggested column mappings with confidence and reasons, a 20-row preview through the real validators, approval, then the import report. Uploading the same file twice is refused.
7. **Settings.** Saved mappings and their versions, the rate card in force (read only), partners and terms.
8. **Activity.** Your own audit trail.

Then **sign in as the Co-founder.** Everything above is visible, but there's no upload panel, no run button and no claim actions. The Co-founder can edit partners and sees the whole audit log.

To see partner matching end to end, upload settlement, COD, freight, return and warehouse files that reference the ISO-TEST order IDs or tracking IDs, then run reconciliation again. A run only reads imported records and writes its own results.

## 3. Run it locally

### Prerequisites

- Python 3.11
- Node.js 22 (Next.js 16's Tailwind tooling fails on Node 20)
- Docker Desktop, for PostgreSQL 16
- Git

**Don't keep the repository in OneDrive or another cloud-sync folder.** Synced files become placeholders that Node reports as links, and Playwright skips them without a warning. `playwright.config.ts` refuses to run when that happens.

### PostgreSQL

```powershell
# From the repository root. Starts Postgres 16 on localhost:5433, with the local-only user and database
# defined in docker-compose.yml. backend/.env.example already points at it.
docker compose up -d --wait
```

### Backend (FastAPI)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
copy .env.example .env
# In .env, set JWT_SECRET (32+ characters), SEED_FINANCE_PASSWORD and SEED_FOUNDER_PASSWORD
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python scripts\seed_foundation.py
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

### Frontend (Next.js)

```powershell
cd frontend
npm ci
npm run dev        # http://localhost:3000, proxies /api to http://localhost:8000
```

Sign in with the two demo emails and the passwords you put in `backend/.env`.

### Tests

```powershell
# Backend: 472 tests. Uses a separate recon_test database on the Docker Postgres.
cd backend
.\.venv\Scripts\python -m pytest

# Browser: 19 Playwright tests. Needs the API and frontend running.
cd frontend
npx playwright install chromium
$env:SEED_FINANCE_PASSWORD = "..."; $env:SEED_FOUNDER_PASSWORD = "..."
$env:E2E_ALLOW_WRITES = "true"      # local only. Leave unset against a deployment
npx playwright test
```

The test suite drops the whole schema, so it refuses any database whose name doesn't contain `test` or `scratch`.

## 4. Migrations and seed data

**Migrations** are Alembic revisions `0001` (foundation), `0002` (ingestion) and `0003` (claim evidence). In production they run automatically at startup through `scripts/start.py`, under a Postgres advisory lock, so two starting instances can't migrate at once. Locally, run `alembic upgrade head`.

Don't use `alembic downgrade` on real data. Downgrading `0002` deletes imported orders. Create a Neon branch before a release as a restore point instead. Each migration only adds to the schema, so the previous commit still runs on the newer one.

**Seed** (`scripts/seed_foundation.py`) adds the two demo users, nine partners, six sales channels, settings including the ₹10 threshold, and the demo rate card. It only adds what's missing, and never changes an existing row or password.

| Option | What it does |
|---|---|
| *(none)* | Adds anything missing. Needs `SEED_FINANCE_PASSWORD` and `SEED_FOUNDER_PASSWORD` only for users that don't exist yet |
| `--rate-card-only` | Adds only "Demo rate card" v1 with its settlement, freight and tax rules. Writes nothing else and reads no passwords. Does nothing if the card exists |
| `--reset-passwords` | Also resets existing demo users' passwords. Only for deliberately changing them |

Render's free tier has no shell, so seeding the deployment runs from a developer machine against the direct Neon URL:

```powershell
cd backend
$deploy = "C:\path\to\.env.deploy"            # DATABASE_URL and DATABASE_URL_DIRECT, outside the repository
$creds  = "C:\path\to\demo-credentials.env"   # SEED_* passwords, outside the repository
.\.venv\Scripts\python scripts\generate_demo_credentials.py $creds   # random passwords, never printed or overwritten
.\.venv\Scripts\python scripts\with_env.py --env-file $deploy --env-file $creds -- .\.venv\Scripts\python scripts\seed_foundation.py
```

`scripts/with_env.py` loads those files for one command and masks secrets in its output. Add `--dns 1.1.1.1` if your router can't resolve Neon's hostname (`getaddrinfo failed`).

## 5. Deployment architecture

```
Browser
   |
Next.js 16 on Vercel Hobby  -- same-origin /api/* rewrite -->  FastAPI on Render Free (Singapore)
                                                                  |  API, RBAC, one background job thread
                                          pooled URL (requests) --+-- direct URL (jobs, migrations)
                                                                  |
                                                        Neon Free PostgreSQL 16
                                              (data, job queue, compressed uploaded files)
```

- The browser only talks to the Vercel origin. Next.js rewrites `/api/*` to Render, so the session cookie is first-party.
- Long work (file analysis, validation, import, reconciliation) runs as persisted jobs in Postgres. The API returns a job ID and the UI polls it.
- `/api/healthz` never touches the database, so health checks don't wake Neon.
- Pushing to `main` redeploys both services. Render only redeploys when files under `backend/` change.

### Deploying from scratch

1. **Neon.** Create a project (Postgres 16, AWS Singapore). Copy the pooled connection string (host contains `-pooler`) and the direct one. Save them in a file outside the repository as `DATABASE_URL` and `DATABASE_URL_DIRECT`. Optionally check them:

   ```powershell
   .\.venv\Scripts\python scripts\with_env.py --env-file $deploy -- .\.venv\Scripts\python scripts\verify_database.py urls
   ```

2. **Render.** New > Blueprint, and select the repository. Render reads `render.yaml` (root directory `backend`, start command `python scripts/start.py`, health check `/api/healthz`). Enter `DATABASE_URL` and `DATABASE_URL_DIRECT` when prompted. `JWT_SECRET` is generated. Migrations run on start.
3. **Seed** from your machine, as in section 4.
4. **Vercel.** Import the repository with root directory `frontend`. Set `API_ORIGIN` to the Render URL, with no trailing slash. The build fails on purpose if it's missing.
5. **Verify.** Open `/api/healthz`, sign in as both users, and run the read-only browser checks against the deployment:

   ```powershell
   $env:E2E_BASE_URL = "https://<project>.vercel.app"   # leave E2E_ALLOW_WRITES unset
   .\.venv\Scripts\python scripts\with_env.py --env-file $creds --cwd ..\frontend -- npx playwright test
   ```

In production the API refuses to start if `DATABASE_URL` or `DATABASE_URL_DIRECT` is missing, malformed, swapped or identical. The log names the variable and the problem, never its value.

## 6. Environment variables

Names only. Values live in the hosting dashboards or in local files outside the repository.

| Where | Variable | Purpose |
|---|---|---|
| Render | `DATABASE_URL` | Neon pooled connection, for API requests |
| Render | `DATABASE_URL_DIRECT` | Neon direct connection, for migrations and jobs. Required in production |
| Render | `JWT_SECRET` | Signs session tokens. At least 32 characters |
| Render | `APP_ENV` | `production` disables the API docs and requires secure cookies |
| Render | `COOKIE_SECURE` | `true` over HTTPS |
| Render | `DEMO_MODE` | Shows the synthetic data banner and marks uploads as synthetic |
| Render | `MAX_UPLOAD_MB` | Upload size limit, default 25 |
| Render | `ENABLE_DIAGNOSTICS` | Enables the upload probe. `false` in production |
| Render | `PYTHON_VERSION` | Python runtime for Render |
| Render | `ENABLE_JOB_WORKER` | Optional, default `true`. Runs the background job thread |
| Render | `MAX_IMPORT_ROWS` | Optional, default 200000 |
| Render | `ACCESS_TOKEN_MINUTES` | Optional, session length, default 480 |
| Render | `GEMINI_API_KEY` | Optional. Without it, mapping suggestions are rule-based |
| Render | `GEMINI_MODEL` | Optional, default `gemini-2.5-flash` |
| Vercel | `API_ORIGIN` | The Render URL, used by the `/api` rewrite |
| Local | `SEED_FINANCE_PASSWORD`, `SEED_FOUNDER_PASSWORD` | Demo user passwords for the seed. Never set on Render |
| Local | `E2E_BASE_URL`, `E2E_ALLOW_WRITES` | Browser test target, and whether tests may write. Writes are for local databases only |

## 7. Free-tier behaviour and limits

- **Cold starts:** about a minute after 15 idle minutes on Render. API calls retry safe requests after a 502 or 504.
- **Neon:** 1 GB storage, and monthly compute and transfer quotas. Going over suspends the database, so large benchmark files shouldn't be loaded into the demo.
- **Performance:** the 100,000-row benchmarks were measured locally (see `docs/benchmarks.md`). Deployed import performance and the largest upload that passes through Vercel haven't been measured.
- **Vercel Hobby** is for non-commercial use. A real client deployment needs paid plans.
