# D2C Reconciliation Platform

Finds money a D2C brand is owed, order by order, across marketplace settlements, COD remittances, freight invoices and returns. It then helps the finance team claim that money back.

Built as a take-home for Isocode. The client is a Bengaluru home textiles brand with about 30,000 orders a month, sold through its website, three marketplaces and two quick-commerce platforms, and shipped through two courier aggregators.

> **Status: Milestone 1 (foundation) complete locally, deployment pending.** Sign-in, roles, the database schema, partners and the audit log work. File import, reconciliation, claims and the dashboard aren't built yet. All data is synthetic.

## Scope

The brief asks for a small part built well. The priority is one complete workflow:

**Upload → Mapping approval → Import → Reconciliation → Discrepancy review → Claim creation → Evidence export**

A money-at-risk dashboard, calculated from database records, sits on top of it.

Suggestions never change money: a person approves every column mapping, and all financial calculations are deterministic code. Mapping suggestions come from a rule-based matcher, with Gemini added behind the same interface if time permits. AI features are only described as AI if a real provider integration exists.

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

## Documentation

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | System design, schema, canonical model, reconciliation engine, API, screens, free-tier deployment, job guarantees |
| [docs/assumptions.md](docs/assumptions.md) | What the brief leaves open, what we assumed and what the client must confirm |
| [docs/implementation-plan.md](docs/implementation-plan.md) | Four milestones to the 9 November deadline, priorities, cut order, performance plan |
| [deployment.md](deployment.md) | How to deploy to Vercel, Render and Neon |

## Stack

| Layer | Technology | Hosting (free tiers) |
|---|---|---|
| Frontend | Next.js, TypeScript, Tailwind, shadcn/ui, Recharts | Vercel Hobby |
| Backend | FastAPI, SQLAlchemy, Alembic, pandas | Render Free |
| Database | PostgreSQL | Neon Free |

## Performance

The target is 100,000 rows in under a minute. No benchmark has been run yet, because the import pipeline arrives in Milestone 2. Local and deployed results will be published here separately, as measured. Passing locally doesn't mean the free hosting meets the target.

## Data

This repository only ever contains synthetic data. Secrets live in environment variables and are never committed.
