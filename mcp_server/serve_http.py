"""HTTP entry point for Railway-hosted MCP server.

Creates a Starlette app with:
- Static bearer token auth (no OAuth discovery — avoids Claude Code bug)
- /health endpoint (unauthenticated)
- FastMCP streamable-http handler (authenticated)
- Postgres connection lifecycle
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp_server.db import DB
from mcp_server.server_http import db, mcp

EDA_AUTH_TOKEN = os.environ.get("EDA_AUTH_TOKEN", "")


def _env_first(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return ""


def build_info() -> dict[str, str]:
    """Return non-secret deploy metadata for health checks."""
    return {
        "commit_sha": _env_first("RAILWAY_GIT_COMMIT_SHA", "GITHUB_SHA", "SOURCE_VERSION"),
        "branch": _env_first("RAILWAY_GIT_BRANCH", "GITHUB_REF_NAME"),
        "repo": _env_first("RAILWAY_GIT_REPO_NAME", "GITHUB_REPOSITORY"),
        "repo_owner": _env_first("RAILWAY_GIT_REPO_OWNER"),
        "deployment_id": _env_first("RAILWAY_DEPLOYMENT_ID"),
        "service_id": _env_first("RAILWAY_SERVICE_ID"),
        "service_name": _env_first("RAILWAY_SERVICE_NAME"),
        "environment_id": _env_first("RAILWAY_ENVIRONMENT_ID"),
        "environment_name": _env_first("RAILWAY_ENVIRONMENT_NAME"),
        "project_id": _env_first("RAILWAY_PROJECT_ID"),
    }


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Simple static bearer token check. No OAuth, no WWW-Authenticate header."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path in {"/health", "/estimates"}:
            return await call_next(request)

        if not EDA_AUTH_TOKEN:
            return JSONResponse(
                {"error": "EDA_AUTH_TOKEN not configured"},
                status_code=500,
            )

        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {EDA_AUTH_TOKEN}":
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        return await call_next(request)


async def health(request: Request) -> JSONResponse:
    pending = 0
    db_ok = db.pool is not None
    if db_ok:
        try:
            pending = await db.count_pending()
        except Exception:
            db_ok = False
    return JSONResponse({
        "status": "ok" if db_ok else "degraded",
        "db": db_ok,
        "pending_jobs": pending,
        "auth_configured": bool(EDA_AUTH_TOKEN),
        "build": build_info(),
    })


async def estimates(request: Request) -> JSONResponse:
    """Recent estimate_complexity calls with their spec_issues — for monitoring suggestion quality."""
    if db.pool is None:
        return JSONResponse({"error": "db not connected"}, status_code=503)
    limit = min(int(request.query_params.get("limit", "50")), 200)
    issues_only = request.query_params.get("issues", "false").lower() == "true"
    rows = await db.recent_estimates(limit)
    if issues_only:
        rows = [r for r in rows if r["spec_issues"]]
    return JSONResponse(rows)


def create_app() -> Starlette:
    mcp_app = mcp.streamable_http_app()

    # Inject our routes and middleware into the FastMCP app so its lifespan
    # (which starts the session manager task group) runs properly.
    mcp_app.routes.insert(0, Route("/health", health))
    mcp_app.routes.insert(1, Route("/estimates", estimates))
    mcp_app.middleware_stack = None  # force rebuild
    mcp_app.user_middleware.insert(0, Middleware(BearerTokenMiddleware))

    # Wrap the original lifespan to also manage Postgres
    original_lifespan = mcp_app.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(app):
        database_url = os.environ.get("DATABASE_URL")
        if database_url:
            await db.connect(database_url)
            print("Postgres connected", flush=True)
        else:
            print("WARNING: No DATABASE_URL — job features disabled", flush=True)
        try:
            async with original_lifespan(app) as state:
                yield state
        finally:
            await db.close()

    mcp_app.router.lifespan_context = combined_lifespan

    return mcp_app


def main() -> None:
    port = int(os.environ.get("PORT", 8000))
    app = create_app()
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    import asyncio
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
