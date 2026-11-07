# D2C Reconciliation Platform

Finds money a D2C brand is owed, order by order, across marketplace settlements, COD remittances, freight invoices and returns. It then helps the finance team claim that money back.

Built as a take-home for Isocode. The client is a Bengaluru home textiles brand with about 30,000 orders a month, sold through its website, three marketplaces and two quick-commerce platforms, and shipped through two courier aggregators.

> **Status: Milestone 3 (reconciliation and claims) built and tested locally, not yet deployed.** Milestone 2 (ingestion) is deployed. Working end to end: sign-in and roles, partners, the audit log, the import workflow, deterministic reconciliation with an explanation for every order, returns tracking, claims with CSV evidence export, and a summary of tax withheld. The dashboard polish of Milestone 4 isn't done yet. All data is synthetic.

## Scope

The brief asks for a small part built well. The priority is one complete workflow:

**Upload → Mapping approval → Import → Reconciliation → Discrepancy review → Claim creation → Evidence export**

A money-at-risk dashboard, calculated from database records, sits on top of it.

Suggestions never change money: a person approves every column mapping, and all financial calculations are deterministic code. Mapping suggestions come from a rule-based matcher. An optional Gemini integration sits behind the same interface and is used only if `GEMINI_API_KEY` is set. It's tested against mocked responses, not yet against the live API, and the screen always says which one produced the suggestions.

## Run it locally

You need Python 3.11, Node 22 and Docker Desktop.

```powershell
# 1. Database (from the repository root)
docker compose up -d --wait

# 2. API
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
copy .env.example .env
# In .env, set JWT_SECRET, SEED_FINANCE_PASSWORD and SEED_FOUNDER_PASSWORD
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python scripts\seed_foundation.py
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000

# 3. Frontend (in a second terminal)
cd frontend
npm install
npm run dev
```

Open http://localhost:3000 and sign in as either demo user, using the passwords you set:

| Role | Email |
|---|---|
| Finance Executive | `finance@loomandfold.example` |
| Co-founder | `founder@loomandfold.example` |

## Tests

```powershell
# Backend. Needs the Docker database, and uses a separate recon_test database.
cd backend
.\.venv\Scripts\python -m pytest

# Browser tests. Needs the API and frontend running, with the seed passwords set.
cd frontend
npx playwright install chromium
$env:SEED_FINANCE_PASSWORD = "..."; $env:SEED_FOUNDER_PASSWORD = "..."
$env:E2E_ALLOW_WRITES = "true"   # leave unset on a deployment
npx playwright test
```

**Don't keep the repository in OneDrive or another cloud-sync folder.** OneDrive turns synced files into placeholders that Node reports as links, and Playwright skips them without a warning, so a run would pass with fewer tests. `playwright.config.ts` refuses to run when that happens. Clone or move the repository to an unsynced folder, such as `C:\dev`, and expect all 19 browser tests to run.

## Documentation

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | System design, schema, canonical model, reconciliation engine, API, screens, free-tier deployment, job guarantees |
| [docs/assumptions.md](docs/assumptions.md) | What the brief leaves open, what we assumed and what the client must confirm |
| [docs/implementation-plan.md](docs/implementation-plan.md) | Four milestones to the 9 November deadline, priorities, cut order, performance plan |
| [docs/benchmarks.md](docs/benchmarks.md) | Measured 100k-row performance, and how to reproduce it |
| [deployment.md](deployment.md) | How to deploy to Vercel, Render and Neon |

## Stack

| Layer | Technology | Hosting (free tiers) |
|---|---|---|
| Frontend | Next.js, TypeScript, Tailwind, shadcn/ui, Recharts | Vercel Hobby |
| Backend | FastAPI, SQLAlchemy, Alembic, pandas | Render Free |
| Database | PostgreSQL | Neon Free |

## Performance

The target is 100,000 rows in under a minute. Locally, every CSV import met it, and one of two XLSX imports didn't. Reconciling 100,000 orders takes about two minutes the first time and 75 seconds after that. See [docs/benchmarks.md](docs/benchmarks.md) for the figures and their caveats. Deployed performance on the free tiers hasn't been measured.

## Data

This repository only ever contains synthetic data. Secrets live in environment variables and are never committed.
