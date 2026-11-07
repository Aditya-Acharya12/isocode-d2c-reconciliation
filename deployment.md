# Deployment

Everything runs on free tiers: Neon for the database, Render for the API, and Vercel for the frontend. Deploy them in that order, because each one needs a value from the one before.

> The deployed URLs and demo passwords will be added here once the deployment is live.

## 1. Neon (database)

1. Create a project in the **AWS Singapore** region, on Postgres 16. This is what the project is tested on.
2. From the project's connection details, copy two connection strings:
   - **Pooled**: the host contains `-pooler`. This becomes `DATABASE_URL`.
   - **Direct**: the same string without `-pooler`. This becomes `DATABASE_URL_DIRECT`.
3. Save both in a file **outside the repository**, and outside any synced folder such as OneDrive:

   ```
   DATABASE_URL=<pooled string>
   DATABASE_URL_DIRECT=<direct string>
   ```

Migrations and background jobs need the direct URL, because session-level advisory locks and staging tables don't work through the pooler.

Local commands reach Neon through `scripts/with_env.py`, which loads that file for one command and masks secrets in the output. The app itself only reads ordinary environment variables, so the file's path never reaches production settings.

```powershell
cd backend
$deploy = "C:\path\to\.env.deploy"
.\.venv\Scripts\python scripts\with_env.py --env-file $deploy -- .\.venv\Scripts\python scripts\verify_database.py urls
.\.venv\Scripts\python scripts\with_env.py --env-file $deploy -- .\.venv\Scripts\python scripts\verify_database.py concurrent-migrate
.\.venv\Scripts\python scripts\with_env.py --env-file $deploy -- .\.venv\Scripts\python scripts\verify_database.py schema
```

**If the hostname won't resolve (`getaddrinfo failed`):** some home routers fail to resolve `*.aws.neon.tech`, even though the hostname is valid. Add `--dns 1.1.1.1` to any `with_env.py` command. It looks the address up on that DNS server and passes it to the driver as `hostaddr`, while TLS and Neon's routing keep using the real hostname. This only affects local commands, not the deployed app.

**Never point the test suite at this database.** The tests drop the whole schema, so they refuse any database whose name doesn't contain `test` or `scratch`. To run them on Neon, use `verify_database.py scratch-pytest`, which creates a temporary database and always deletes it.

## 2. Render (API)

1. Choose **New > Blueprint** and select this repository. Render reads `render.yaml`.
2. Enter `DATABASE_URL` and `DATABASE_URL_DIRECT` when prompted. `JWT_SECRET` is generated for you.
3. Wait for the deploy, then open `https://<service>.onrender.com/api/healthz`. It should return `{"status":"ok",...}`.

Migrations run automatically at each start.

## 3. Seed the demo users

Render's free tier has no shell, so this runs from your own machine:

```powershell
cd backend
$creds = "C:\path\to\demo-credentials.env"
.\.venv\Scripts\python scripts\generate_demo_credentials.py $creds
.\.venv\Scripts\python scripts\with_env.py --env-file $deploy --env-file $creds -- .\.venv\Scripts\python scripts\seed_foundation.py
```

The generator creates random passwords without printing them, and never overwrites an existing file. Keep that file outside the repository. It's safe to run the seed again: it only adds what's missing, and never changes an existing user's password unless you pass `--reset-passwords`.

## 4. Vercel (frontend)

1. Import this repository, and set the **Root Directory** to `frontend`.
2. Add the environment variable `API_ORIGIN`, set to the Render URL with no trailing slash. The build fails on purpose if it's missing.
3. Deploy.

## 5. Verify

```powershell
# From backend/
# Sign-in and role checks, through a real browser. Leave E2E_ALLOW_WRITES unset.
$env:E2E_BASE_URL = "https://<project>.vercel.app"
.\.venv\Scripts\python scripts\with_env.py --env-file $creds --cwd ..\frontend -- npx playwright test

# How large an upload passes through intact
.\.venv\Scripts\python scripts\with_env.py --env-file $creds -- .\.venv\Scripts\python scripts\check_upload_limit.py https://<project>.vercel.app
```

Set `ENABLE_DIAGNOSTICS` to `false` on Render once the upload limits have been measured.

## Updating an existing deployment

Pushing to `main` redeploys Render and Vercel. Migrations run automatically at start, under the advisory lock.

**Startup checks:** in production, the API refuses to start if `DATABASE_URL` or `DATABASE_URL_DIRECT` is missing, malformed, swapped or identical. The log names the variable and the problem, such as `DATABASE_URL_DIRECT starts with a variable name`, and never repeats its value. Values must be the bare connection string, with no quotes, spaces, `psql` or variable name.

**Rolling back:** don't use `alembic downgrade` on real data. Downgrading `0002` deletes imported order data, and refuses to run if records depend on a warehouse. Instead, create a Neon branch before migrating as a restore point. The older code also runs on the newer schema, because `0002` only adds to it, so redeploying the previous commit doesn't need a downgrade.

After a release that changes the seed, run it again from your machine. It only adds what's missing, such as the `Warehouse` partner or the demo rate card. It never changes an existing row, and leaves existing users' passwords alone, so the credentials file is only needed if a demo user is missing.

```powershell
.\.venv\Scripts\python scripts\with_env.py --env-file $deploy --env-file $creds --dns 1.1.1.1 -- .\.venv\Scripts\python scripts\seed_foundation.py
```

| Option | What it does |
|---|---|
| *(none)* | Adds missing users, partners, channels, settings and the demo rate card. Existing passwords are unchanged |
| `--rate-card-only` | Adds only the demo rate card and its tax rules. Reads users, partners, channels and settings, and writes none of them. Needs no credentials file |
| `--reset-passwords` | Also sets existing demo users' passwords from the credentials file. Only use this to change them on purpose |

`--rate-card-only` does nothing if a card named "Demo rate card" already exists, so it never overwrites rates. If a partner or channel the card needs is missing, it stops and writes nothing.

### Releasing Milestone 3 (reconciliation and claims)

1. Create a Neon branch as a restore point.
2. Push. Migration `0003` runs at startup. It only adds the nullable `claims.evidence` column, so existing rows are untouched and the previous commit still runs on the new schema.
3. Add the demo rate card. This adds "Demo rate card" v1 with its settlement, freight and tax rules, and changes nothing else. It doesn't read the credentials file, so it can't touch any password. Without a published rate card, every order shows as Needs review.

   ```powershell
   cd backend
   $deploy = "C:\path\to\.env.deploy"   # outside the repository, as in section 1
   .\.venv\Scripts\python scripts\with_env.py --env-file $deploy --dns 1.1.1.1 -- .\.venv\Scripts\python scripts\seed_foundation.py --rate-card-only
   ```

   It prints either `Added "Demo rate card" v1. Nothing else was changed.` or that the card already exists. `--dns 1.1.1.1` is only needed if your router can't resolve Neon's hostname.
4. Sign in as the Finance Executive, open **Reconciliation** and choose **Run reconciliation**. A run only reads imported records and writes its own results, discrepancies and audit entry. It doesn't change orders or partner records, apart from filling in empty order links.

## Verify the ingestion workflow

Use the Finance Executive account, and small synthetic files only. Check Neon's storage afterwards, as the free plan allows 1 GB.

1. Under **Imports**, choose **Orders** and **Website**, drop in a small CSV, and upload it. The page should show "Reading the file…" and then the mapping table.
2. Check the confidence and reasons, change a mapping by hand, and choose **Validate these rows**.
3. Check the counts and row issues in the summary, then choose **Approve and import**.
4. Confirm the status reaches **Imported** and the row counts are right. Download the row issues CSV if any rows failed.
5. Upload the same file again. It should be refused, with a link to the earlier upload.
6. Sign in as the Co-founder. The history should be visible, with no upload panel.

**Testing a restart:** start an import of a larger file, and restart the service from Render's dashboard while it runs. After about two minutes, the job should return to the queue and finish, with no duplicate rows.

## Environment variables

| Service | Variable | Purpose |
|---|---|---|
| Render | `DATABASE_URL` | Neon pooled URL, for API requests |
| Render | `DATABASE_URL_DIRECT` | Neon direct URL, for migrations and jobs. Required in production, with no fallback |
| Render | `JWT_SECRET` | Signs session tokens. At least 32 characters |
| Render | `APP_ENV` | `production` turns off the API docs and requires secure cookies |
| Render | `COOKIE_SECURE` | Must be `true` over HTTPS |
| Render | `MAX_UPLOAD_MB` | Upload limit, default 25 |
| Render | `DEMO_MODE` | Shows the synthetic data banner |
| Render | `ENABLE_DIAGNOSTICS` | Enables the upload probe. Keep off except when testing |
| Render | `ENABLE_JOB_WORKER` | Optional, default `true`. Runs the background job thread |
| Render | `MAX_IMPORT_ROWS` | Optional, default 200000. Largest file that can be imported |
| Render | `GEMINI_API_KEY` | Optional. Without it, suggestions are rule-based. Synthetic data only on the free tier |
| Render | `GEMINI_MODEL` | Optional, default `gemini-2.5-flash` |
| Vercel | `API_ORIGIN` | The Render URL, used by the `/api` rewrite |

## What to expect on free tiers

- **First request after 15 idle minutes:** Render takes about a minute to wake. The login page says so and enables sign-in when ready.
- **Database resume:** Neon suspends after 5 idle minutes, and resumes on the next query.
- **Performance:** the free instance is small. Benchmarks from a local machine say nothing about this hosting, and deployed figures are reported separately.
