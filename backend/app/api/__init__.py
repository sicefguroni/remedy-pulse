"""app/api — the Phase 7 HTTP API layer (7.1/7.4).

Implements every endpoint in docs/api-contract.md against the real Phase
2-6 schema/repository/auth stack. See app.api.main for the FastAPI app
itself, app.api.deps for the shared auth/DB dependencies, and
app.api.routes/ for one module per resource.

This package needs two new runtime dependencies not yet in
requirements.txt: fastapi and uvicorn (an ASGI server to actually run the
app). Not added here - see this phase's final report for exact pinned
versions; requirements.txt is outside this package's file ownership.

Run with (from backend/, with those two packages installed):
    uvicorn app.api.main:app
"""
