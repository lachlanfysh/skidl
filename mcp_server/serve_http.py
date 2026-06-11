"""HTTP entry point for Railway-hosted MCP server.

Creates a Starlette app with:
- Static bearer token auth (no OAuth discovery — avoids Claude Code bug)
- /health endpoint (unauthenticated)
- FastMCP streamable-http handler (authenticated)
- Postgres connection lifecycle
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from mcp_server.db import DB
from mcp_server.server_http import db, mcp

EDA_AUTH_TOKEN = os.environ.get("EDA_AUTH_TOKEN", "")


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Simple static bearer token check. No OAuth, no WWW-Authenticate header."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
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
    })


@asynccontextmanager
async def lifespan(app):
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        await db.connect(database_url)
        print("Postgres connected", flush=True)
    else:
        print("WARNING: No DATABASE_URL — job features disabled", flush=True)
    yield
    await db.close()


def create_app() -> Starlette:
    mcp_app = mcp.streamable_http_app()

    app = Starlette(
        routes=[
            Route("/health", health),
            Mount("/mcp", app=mcp_app),
        ],
        middleware=[
            Middleware(BearerTokenMiddleware),
        ],
        lifespan=lifespan,
    )
    return app


def main() -> None:
    port = int(os.environ.get("PORT", 8000))
    app = create_app()
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    import asyncio
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
