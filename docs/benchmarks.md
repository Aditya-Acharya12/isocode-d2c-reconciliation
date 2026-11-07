# Benchmarks

The target is 100,000 rows in under a minute locally. This records what was actually measured, including where the target was missed.

**Result: met for CSV imports, missed for one XLSX import, and missed by most files for the whole workflow.** Reconciliation is reported [separately below](#reconciliation-at-100000-orders): about 122 seconds the first time and 75 seconds after that. Measured as assumption A23 defines the target, all six CSV imports finished within 60 seconds (38–54 seconds), and so did one of the two XLSX imports (54 seconds, against 64 for the other). From upload through to the end of import, only returns, warehouse receipts and the update case stayed under 60 seconds. All of it was measured on a machine with heavy background load, described below, so a clean run is still needed.

## How it was measured

| | |
|---|---|
| **Date** | 6 November 2026 |
| **Machine** | Intel Core i7-1255U, 12 logical CPUs, 15.7 GB RAM, Windows 11 |
| **Software** | Python 3.11.5, pandas 3.1.1, PostgreSQL 16.15 in a local Docker container with default settings |
| **Data** | 100,000 generated rows per file, with 0.5% deliberately invalid |
| **Code path** | The real job code, in-process, against real Postgres. HTTP isn't included |

**Important limitation: the machine was busy.** OneDrive was syncing the project folder throughout, using about 190% of one core, with total CPU around 60–85% before any benchmark work. An earlier run, with type checking and linting also running, took up to twice as long. These figures are therefore likely pessimistic, but how much faster a quiet machine would be hasn't been measured.

## Results

Times are in seconds. **Import** is from an approved import starting to all rows being committed, which is how A23 defines the target. **Whole workflow** adds the upload, analysis and validation steps.

| File (100,000 rows) | Size | Upload | Analysis | Validation | Import | Whole workflow | Peak memory |
|---|---:|---:|---:|---:|---:|---:|---:|
| Orders, CSV | 10.2 MB | 1.7 | 4.4 | 10.2 | **53.3** | 69.6 | 402 MB |
| Settlements, CSV | 7.6 MB | 1.5 | 5.9 | 17.8 | **51.3** | 76.5 | 419 MB |
| COD remittances, CSV | 5.9 MB | 0.6 | 5.4 | 14.4 | **45.7** | 66.1 | 325 MB |
| Freight invoices, CSV | 6.8 MB | 0.5 | 6.2 | 14.3 | **54.0** | 75.0 | 341 MB |
| Returns, CSV | 4.7 MB | 0.3 | 3.3 | 11.0 | **40.4** | 55.0 | 241 MB |
| Warehouse receipts, CSV | 3.6 MB | 0.4 | 3.9 | 6.2 | **37.8** | 48.3 | 238 MB |
| Orders, XLSX | 6.0 MB | 1.5 | 16.0 | 20.3 | **53.6** | 91.4 | 350 MB |
| Settlements, XLSX | 6.0 MB | 1.4 | 18.1 | 31.2 | **64.1** | 114.9 | 321 MB |
| Orders, CSV, updating existing rows | 10.2 MB | 0.5 | 3.8 | 5.7 | **15.5** | 25.4 | 290 MB |

Every file imported exactly 99,500 rows and rejected exactly the 500 planted bad rows. Peak memory is for the whole process, including what the benchmark itself holds.

Full figures, with the time of each step inside the import, are in [`backend/benchmarks/results/ingestion_100000.json`](../backend/benchmarks/results/ingestion_100000.json).

## Where the time goes

The database insert dominates, at 30–44 seconds of a typical import. Profiling an isolated 100,000-row insert into warehouse receipts, under the same load, gave:

| Step | Time |
|---|---:|
| Writing rows as CSV | 0.5 s |
| `COPY` into the staging table | 0.1 s |
| `INSERT ... SELECT`, including indexes | about 18 s |
| Foreign key checks, on `partner_id` and `source_file_id` | about 14 s |

Parsing and validating take much less, so the pandas and `COPY` design isn't the bottleneck. Foreign key checks run once per row in Postgres. They were kept deliberately, because they protect the link from every financial record back to its source file, and disabling them needs superuser rights that hosted Postgres doesn't give.

## Limitations

- **Deployed performance hasn't been measured.** These figures say nothing about Render Free and Neon Free, which have far less CPU and add network latency. Expect them to be much slower.
- **Memory on Render Free is a real risk.** The peak of 238–419 MB leaves little margin if the instance really has 512 MB, and this hasn't been tested there. A failure there would show up as an interrupted job.
- **Storage:** 100,000 rows plus the stored file take roughly 40 MB in Postgres. Neon Free allows 1 GB per project, so large benchmark files shouldn't be loaded into the demo database.
- **The target isn't a guarantee.** A smaller machine, or a table with many more existing rows, will be slower.

## Reconciliation at 100,000 orders

Measured on 7 November 2026, on the same machine and with the same caveat about background load. After importing the six CSV files above, the benchmark reconciles everything with the demo rate card, through the real job runner. That's 100,000 orders with about 99,500 settlement lines, COD remittances, freight lines, returns and warehouse receipts each.

**Result: about 122 seconds for the first run and about 75 seconds for a rerun, so neither is under one minute.** The one-minute target is read as applying to imports (A23). The brief doesn't say whether it also covers reconciliation, so this doesn't claim to meet or miss it. If the target does cover reconciliation, it's missed.

| Run | Total | Link | Load | Evaluate | Save results | Save discrepancies | Peak memory |
|---|---:|---:|---:|---:|---:|---:|---:|
| First run, linking 500,000 records | **122.5 s** | 48.8 | 14.2 | 15.1 | 30.5 | 13.4 | 364 MB |
| Second run, same data | **75.0 s** | 8.2 | 14.4 | 14.8 | 31.5 | 5.9 | 219 MB |

- The generated amounts are random, so almost every order is an exception (51% Needs review, 48% Freight overcharged). That's the most work for the engine. Real data would mostly settle.
- **Memory stays flat** because orders are evaluated 5,000 at a time. Before that change, a 20,000-order run already peaked at 422 MB. Peak memory includes about 145 MB the process holds before the run starts.
- **Linking is mostly a one-time cost.** A later run only fills links that are still empty.
- **Storage.** Result rows average about 1.5 KB, mostly the explanation, so 100,000 orders take about 175 MB live. Only the current run's rows are kept. The previous run's rows wait as dead rows until autovacuum, so allow about twice that. With all six imported files, the scratch database reached 778 MB. **That's more than the Neon free plan allows, so 100,000 orders with every partner file isn't a free-tier workload.**
- Full figures are in [`backend/benchmarks/results/reconciliation_100000.json`](../backend/benchmarks/results/reconciliation_100000.json). Its import figures come from the same run, which skipped XLSX. The recorded ingestion results above are unchanged.

## Reproduce it

Pause OneDrive first, or any other heavy background process, to get a clean figure. Add `--skip-reconcile` to measure imports only.

```powershell
docker compose up -d --wait
cd backend
.\.venv\Scripts\python benchmarks\run_benchmark.py          # 100,000 rows, about 10 minutes
.\.venv\Scripts\python benchmarks\run_benchmark.py --rows 5000   # a quick check
```

It uses its own database, `recon_scratch_benchmark`, on the local Docker Postgres, and doesn't touch the app's data. Generated files go to the system temp folder.
