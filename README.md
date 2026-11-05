# D2C Reconciliation Platform

Repository: `isocode-d2c-reconciliation`

Finds money a D2C brand is owed, order by order, across marketplace settlements, COD remittances, freight invoices and returns. It then helps the finance team claim that money back.

Built as a take-home for Isocode. The client is a Bengaluru home textiles brand with about 30,000 orders a month, sold through its website, three marketplaces and two quick-commerce platforms, and shipped through two courier aggregators.

> **Status: planning complete, implementation not started.** No application code exists yet. This README will gain setup steps, demo credentials and measured benchmarks as those parts are built.

## Scope

The brief asks for a small part built well. The priority is one complete workflow:

**Upload → Mapping approval → Import → Reconciliation → Discrepancy review → Claim creation → Evidence export**

A money-at-risk dashboard, calculated from database records, sits on top of it.

Suggestions never change money: a person approves every column mapping, and all financial calculations are deterministic code. Mapping suggestions come from a rule-based matcher, with Gemini added behind the same interface if time permits. AI features are only described as AI if a real provider integration exists.

## Documentation

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | System design, schema, canonical model, reconciliation engine, API, screens, free-tier deployment |
| [docs/assumptions.md](docs/assumptions.md) | What the brief leaves open, what we assumed and what the client must confirm |
| [docs/implementation-plan.md](docs/implementation-plan.md) | Four milestones to the 9 November deadline, priorities, cut order, performance plan |

## Stack

| Layer | Technology | Hosting (free tiers) |
|---|---|---|
| Frontend | Next.js, TypeScript, Tailwind, shadcn/ui, Recharts | Vercel Hobby |
| Backend | FastAPI, SQLAlchemy, Alembic, pandas | Render Free |
| Database | PostgreSQL | Neon Free |

## Performance

The target is 100,000 rows in under a minute. No benchmark has been run yet. Local and deployed results will be published here separately, as measured. Passing locally doesn't mean the free hosting meets the target.

## Data

This repository only ever contains synthetic data. Secrets live in environment variables and are never committed.
