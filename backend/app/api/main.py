"""app/api/main.py — the Phase 7 FastAPI application.

Implements docs/api-contract.md's every endpoint (see app.api.routes/*
for one module per resource, each documenting its own notes/deviations).

Run with (from backend/, or with backend/ on PYTHONPATH):
    uvicorn app.api.main:app

New dependencies this needs that are NOT yet in requirements.txt:
fastapi and uvicorn - see this phase's final report for the exact pinned
versions verified against; not added to requirements.txt here per this
batch's file-ownership rules (report only, don't add).
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.deps import ApiError
from app.api.routes import auth as auth_routes
from app.api.routes import competitors as competitors_routes
from app.api.routes import emv as emv_routes
from app.api.routes import exports as exports_routes
from app.api.routes import mentions as mentions_routes
from app.api.routes import overview as overview_routes
from app.api.routes import reviews as reviews_routes
from app.api.routes import roster as roster_routes
from app.api.routes import status as status_routes
from app.api.routes import topics as topics_routes

app = FastAPI(title="Remedy Pulse API")

# remedy-pulse-mockup.html (Phase 7's data-driven refactor) is a static
# file opened via file:// or served from a different origin than this
# API, and its apiFetch() sends the session token as an Authorization
# header rather than a cookie (no `credentials: 'include'`) - so a
# wildcard origin is safe here (it would NOT be if this app relied on
# cookie-based auth, where a wildcard origin plus credentialed requests
# is a real CSRF-shaped risk). Restrict this to the actual deployed
# frontend origin(s) once one is chosen instead of "any static file
# anyone opens" - this default is right for local dev and for the
# zero-install demo use case, not for a public production deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    """Renders ApiError as exactly its own JSON payload (docs/api-contract.md's
    `{"error": "..."}` shape), not FastAPI/Starlette's default
    HTTPException `{"detail": ...}` envelope."""
    return JSONResponse(status_code=exc.status_code, content=exc.payload)


@app.get("/health")
def health():
    """No auth, no DB touch, not under /api - a plain liveness probe for
    whatever's watching this process (an uptime pinger keeping a
    free-tier host from sleeping, a platform's own health check before
    routing traffic to it). Deliberately this simple: this project's own
    "say so in the code, or someone will over-build it" rule (4.6) - a
    /ready variant that also checks the database is a real option later
    if a genuine need for it shows up, not preemptively. See
    docs/runbook-deploy-free-tier.md."""
    return {"status": "ok"}


for _router in (
    auth_routes.router,
    overview_routes.router,
    mentions_routes.router,
    reviews_routes.router,
    topics_routes.router,
    competitors_routes.router,
    emv_routes.router,
    roster_routes.router,
    exports_routes.router,
    status_routes.router,
):
    app.include_router(_router, prefix="/api")
