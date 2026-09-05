# Running Remedy Pulse locally (real backend, not the sample-data mockup)

`remedy-pulse-mockup.html` opens directly in a browser with **no setup at
all** and shows sample data — see `docs/README-Remedy-Pulse-Demo.md` for
that path. This doc is for the other mode: the same HTML file, but
logged in against a real FastAPI backend backed by a real Postgres
database, per Phase 7's API layer (`docs/api-contract.md`).

## 1. Start Postgres

```powershell
cd backend
docker compose up -d
```

This starts a `postgres:16-alpine` container (`backend-postgres-1`) on
port 5434 with a persistent volume — see `backend/docker-compose.yml`.
If it's already running, this command is a no-op.

## 2. Create a Python virtual environment (one-time)

There's no committed venv in this repo — create your own:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`.env` already exists in `backend/` with a `DATABASE_URL` that matches
the Docker Compose container above — nothing to fill in for this path
(the other env vars in there are for the ingestion connectors, not
needed just to launch the app).

## 3. Run migrations (one-time, and after any schema change)

```powershell
alembic upgrade head
```

## 4. Create a login user (one-time)

There's no signup UI — accounts are created directly against the
database. Run once, with the venv active, from `backend/`:

```powershell
python -c "from app.db import get_engine; from sqlalchemy.orm import Session; from app.auth import create_user; s = Session(get_engine()); create_user(s, email='you@example.com', password='choose-a-password', display_name='Your Name'); s.commit(); print('created')"
```

Swap in your own email, password, and display name. Run it again with a
different email to create additional teammate accounts (e.g. for the
Assign dropdown's roster).

## 5. Start the API server

```powershell
uvicorn app.api.main:app --reload --port 8000
```

**Port must be 8000** — `remedy-pulse-mockup.html`'s `API_BASE` constant
is hardcoded to `http://localhost:8000/api`. Leave this running in its
own terminal.

## 6. Open the frontend and log in

```powershell
cd ..
start remedy-pulse-mockup.html
```

It opens in demo mode (sample data) by default. Click **Log in** next to
the Demo badge in the top right and sign in with the credentials from
step 4. The page then pulls real data from every tab instead of sample
data, and the demo-only affordances (Demo badge, "+ Simulate mention",
AI-summary "Regenerate") disappear.

## Data volume: what you'll actually see

A fresh local database has almost nothing in it — the tables exist, but
nothing has been ingested. To populate it with real content, run the
ingestion jobs / connectors in `backend/app/jobs/` (or the standalone
`fetch_*.py` scripts in `backend/`), which need real API keys (GNews,
Google Cloud OAuth + Places API) — see `backend/README.md`'s setup steps
for how to get those. Without that, the dashboard will look sparse or
empty even while logged in against a genuinely live API — that's
expected, not a bug.

## If something won't start

- **`docker compose up -d` fails / hangs:** Docker Desktop probably isn't
  running — start it first, then retry.
- **`alembic upgrade head` can't connect:** confirm the container is
  healthy — `docker ps` should show `backend-postgres-1` as `Up
  (healthy)`.
- **The mockup shows "Couldn't reach the API" after logging in:**
  confirm uvicorn is actually running on port 8000, not some other port
  — `API_BASE` is a hardcoded constant in the HTML, not configurable
  from the UI.
- **Login fails with "invalid credentials":** re-run step 4 with a new
  email — the error message is intentionally identical whether the
  email doesn't exist or the password is wrong (see
  `backend/app/api/routes/auth.py`'s own docstring for why).
