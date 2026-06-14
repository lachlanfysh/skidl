"""HTTP entry point for Railway-hosted MCP server.

Creates a Starlette app with:
- Static bearer token auth (no OAuth discovery — avoids Claude Code bug)
- /health endpoint (unauthenticated)
- FastMCP streamable-http handler (authenticated)
- Postgres connection lifecycle
"""

from __future__ import annotations

import asyncio
import hmac
import hashlib
import html
import json
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qs
from urllib import request as urlrequest
from urllib.error import URLError

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

from mcp_server.db import DB
from mcp_server.server_http import db, mcp

EDA_AUTH_TOKEN = os.environ.get("EDA_AUTH_TOKEN", "")
PUBLIC_PATHS = {
    "/",
    "/signup",
    "/signup/thanks",
    "/api/beta-signup",
    "/health",
    "/estimates",
    "/admin/login",
    "/admin/logout",
}
ADMIN_PATH_PREFIXES = ("/admin", "/beta-signups", "/api/beta-signups")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ADMIN_COOKIE = "eda_admin"
MAX_SIGNUP_BODY_BYTES = 20_000
_SIGNUP_ATTEMPTS: dict[str, list[float]] = {}
_ADMIN_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
logger = logging.getLogger("eda-mcp.http")


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


def public_base_url(request: Request | None = None) -> str:
    configured = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if configured:
        return configured
    if request is None:
        return ""
    return str(request.base_url).rstrip("/")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _generate_api_token() -> tuple[str, str, str]:
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    token = f"eda_live_{prefix}_{secret}"
    return token, prefix, _hash_token(token)


def _admin_cookie_signature() -> str:
    if not EDA_AUTH_TOKEN:
        return ""
    return hmac.new(
        EDA_AUTH_TOKEN.encode("utf-8"),
        b"eda-mcp-admin",
        hashlib.sha256,
    ).hexdigest()


def _has_admin_cookie(request: Request) -> bool:
    expected = _admin_cookie_signature()
    cookie = request.cookies.get(ADMIN_COOKIE, "")
    return bool(expected and cookie and hmac.compare_digest(cookie, expected))


def _cookie_secure(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto == "https" or public_base_url(request).startswith("https://")


def _is_admin_path(path: str) -> bool:
    return path.startswith(ADMIN_PATH_PREFIXES)


def _same_origin_post(request: Request) -> bool:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return True
    origin = request.headers.get("origin")
    if not origin:
        return True
    return origin.rstrip("/") == public_base_url(request)


def _signup_rate_limit_per_hour() -> int:
    try:
        return int(os.environ.get("SIGNUP_RATE_LIMIT_PER_HOUR", "8"))
    except ValueError:
        return 8


def _admin_login_rate_limit_per_hour() -> int:
    try:
        return int(os.environ.get("ADMIN_LOGIN_RATE_LIMIT_PER_HOUR", "12"))
    except ValueError:
        return 12


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limited(bucket: dict[str, list[float]], key: str, limit: int, window_s: int = 3600) -> bool:
    if limit <= 0:
        return False
    now = time.time()
    window_start = now - window_s
    attempts = [ts for ts in bucket.get(key, []) if ts >= window_start]
    if len(attempts) >= limit:
        bucket[key] = attempts
        return True
    attempts.append(now)
    bucket[key] = attempts
    return False


def _signup_rate_limited(ip: str) -> bool:
    return _rate_limited(_SIGNUP_ATTEMPTS, ip, _signup_rate_limit_per_hour())


def _admin_login_rate_limited(ip: str) -> bool:
    return _rate_limited(_ADMIN_LOGIN_ATTEMPTS, ip, _admin_login_rate_limit_per_hour())


def _apply_security_headers(response, path: str) -> None:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if path.startswith("/admin") or path.startswith("/api/beta-signups"):
        response.headers.setdefault("Cache-Control", "no-store")


async def _send_email(*, to: str, subject: str, text: str, html_body: str = "") -> bool:
    api_key = os.environ.get("RESEND_API_KEY", "")
    from_email = os.environ.get("BETA_EMAIL_FROM", "")
    if not api_key or not from_email or not to:
        return False

    payload = {
        "from": from_email,
        "to": [to],
        "subject": subject,
        "text": text,
    }
    if html_body:
        payload["html"] = html_body

    def _post() -> None:
        req = urlrequest.Request(
            "https://api.resend.com/emails",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=10) as resp:
            resp.read()

    try:
        await asyncio.to_thread(_post)
        return True
    except (OSError, URLError, TimeoutError) as exc:
        logger.warning("beta email send failed: %s", exc)
        return False


async def _notify_owner(signup: dict, request: Request) -> bool:
    to = os.environ.get("BETA_NOTIFY_EMAIL", "")
    if not to:
        return False
    approve_url = f"{public_base_url(request)}/admin/beta-signups"
    subject = f"EDA-MCP beta request: {signup['email']}"
    text = (
        f"New EDA-MCP beta request\n\n"
        f"Email: {signup['email']}\n"
        f"Name: {signup.get('name') or '-'}\n"
        f"Organization: {signup.get('organization') or '-'}\n"
        f"Use case:\n{signup.get('use_case') or '-'}\n\n"
        f"Review: {approve_url}\n"
    )
    return await _send_email(to=to, subject=subject, text=text)


async def _email_approval(signup: dict, token: str, request: Request) -> bool:
    mcp_url = f"{public_base_url(request)}/mcp"
    subject = "Your EDA-MCP open beta access"
    text = (
        "Your EDA-MCP open beta token is ready.\n\n"
        f"MCP URL: {mcp_url}\n"
        f"Bearer token: {token}\n\n"
        "Keep this token private. It is tied to your usage while the beta is metered.\n"
    )
    html_body = (
        "<p>Your EDA-MCP open beta token is ready.</p>"
        f"<p><strong>MCP URL:</strong> <code>{html.escape(mcp_url)}</code></p>"
        f"<p><strong>Bearer token:</strong> <code>{html.escape(token)}</code></p>"
        "<p>Keep this token private. It is tied to your usage while the beta is metered.</p>"
    )
    return await _send_email(
        to=signup["email"],
        subject=subject,
        text=text,
        html_body=html_body,
    )


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Bearer token check. No OAuth discovery headers."""

    async def dispatch(self, request: Request, call_next):
        request.state.auth = None
        if request.url.path in PUBLIC_PATHS:
            response = await call_next(request)
            _apply_security_headers(response, request.url.path)
            return response

        auth = request.headers.get("authorization", "")
        token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
        admin_path = _is_admin_path(request.url.path)

        if EDA_AUTH_TOKEN and token == EDA_AUTH_TOKEN:
            request.state.auth = {"kind": "owner", "admin": True}
            response = await call_next(request)
            _apply_security_headers(response, request.url.path)
            return response

        if admin_path and _has_admin_cookie(request):
            if not _same_origin_post(request):
                return JSONResponse({"error": "Invalid request origin"}, status_code=403)
            request.state.auth = {"kind": "owner_cookie", "admin": True}
            response = await call_next(request)
            _apply_security_headers(response, request.url.path)
            return response

        if token and db.pool is not None:
            identity = await db.authenticate_api_key(_hash_token(token))
            if identity:
                request.state.auth = {
                    "kind": "api_key",
                    "admin": False,
                    "user": identity["user"],
                    "api_key": identity["api_key"],
                }
                if admin_path:
                    return JSONResponse({"error": "Admin token required"}, status_code=403)
                response = await call_next(request)
                _apply_security_headers(response, request.url.path)
                return response

        if not EDA_AUTH_TOKEN and db.pool is None:
            return JSONResponse(
                {"error": "EDA_AUTH_TOKEN not configured"},
                status_code=500,
            )
        if request.url.path.startswith("/admin") and request.method == "GET":
            return RedirectResponse("/admin/login", status_code=303)
        return JSONResponse({"error": "Invalid token"}, status_code=401)


async def health(request: Request) -> JSONResponse:
    counts = {
        "pending": 0,
        "queued": 0,
        "running": 0,
        "stale_running": 0,
        "active": 0,
    }
    db_ok = db.pool is not None
    if db_ok:
        try:
            counts = await db.job_status_counts()
        except Exception:
            db_ok = False
    return JSONResponse({
        "status": "ok" if db_ok else "degraded",
        "db": db_ok,
        "pending_jobs": counts["pending"],
        "queued_jobs": counts["queued"],
        "running_jobs": counts["running"],
        "stale_running_jobs": counts["stale_running"],
        "active_jobs": counts["active"],
        "auth_configured": bool(EDA_AUTH_TOKEN or db.pool is not None),
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


def _field(values: dict, name: str) -> str:
    return html.escape(str(values.get(name, "") or ""), quote=True)


def _signup_html(
    *,
    values: dict | None = None,
    error: str = "",
    submitted: bool = False,
    created: bool = True,
) -> str:
    values = values or {}
    error_html = (
        f'<div class="notice error" role="alert">{html.escape(error)}</div>'
        if error
        else ""
    )
    submitted_html = ""
    if submitted:
        lead = "You are on the open beta list." if created else "Your signup is already on the list."
        submitted_html = (
            '<div class="notice success" role="status">'
            f"<strong>{lead}</strong> I will review requests and issue personal MCP tokens manually while usage tracking settles."
            "</div>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>EDA-MCP Open Beta</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #191917;
      --muted: #69665f;
      --line: #1f1f1b;
      --hairline: rgba(31, 31, 27, .18);
      --paper: #e8e5dc;
      --panel: #efede6;
      --field: #f8f7f1;
      --accent: #e94f37;
      --accent-dark: #191917;
      --warn: #8f260f;
      --warn-bg: #f3dfd8;
      --ok: #1d5635;
      --ok-bg: #dfeadf;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      color: var(--ink);
      background:
        linear-gradient(90deg, var(--hairline) 1px, transparent 1px),
        linear-gradient(var(--hairline) 1px, transparent 1px),
        var(--paper);
      background-size: 22px 22px;
      line-height: 1.35;
    }}
    main {{
      width: min(1080px, calc(100vw - 28px));
      margin: 0 auto;
      min-height: 100vh;
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(340px, 440px);
      gap: 32px;
      align-items: center;
      padding: 36px 0;
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 9px;
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--ink);
      font-family: "Courier New", Courier, monospace;
      font-size: 12px;
      font-weight: 400;
      letter-spacing: 0;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 20px 0 16px;
      max-width: 760px;
      font-family: "Times New Roman", Times, serif;
      font-size: 82px;
      font-weight: 400;
      line-height: .88;
      letter-spacing: 0;
    }}
    .intro {{
      max-width: 640px;
      color: var(--muted);
      font-size: 18px;
    }}
    .points {{
      margin: 28px 0 0;
      padding: 0;
      display: grid;
      gap: 0;
      list-style: none;
      color: var(--ink);
      border-top: 1px solid var(--line);
      border-left: 1px solid var(--line);
    }}
    .points li {{
      display: grid;
      grid-template-columns: 42px 1fr;
      gap: 0;
      align-items: start;
      min-height: 56px;
      border-right: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
      background: rgba(239,237,230,.72);
    }}
    .mark {{
      width: 42px;
      height: 100%;
      border-right: 1px solid var(--line);
      color: var(--ink);
      display: inline-grid;
      place-items: center;
      font-family: "Courier New", Courier, monospace;
      font-size: 12px;
      font-weight: 400;
      background: #d9d6cd;
    }}
    .points span:last-child {{ padding: 12px 14px; }}
    .form-panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      box-shadow: 8px 8px 0 rgba(25,25,23,.16);
      padding: 18px;
    }}
    .form-panel h2 {{
      margin: 0 0 6px;
      font-family: "Times New Roman", Times, serif;
      font-size: 34px;
      font-weight: 400;
      letter-spacing: 0;
    }}
    .form-panel p {{
      margin: 0 0 18px;
      color: var(--muted);
      font-size: 14px;
    }}
    label {{
      display: block;
      margin: 13px 0 5px;
      font-family: "Courier New", Courier, monospace;
      font-size: 11px;
      font-weight: 400;
      color: var(--ink);
      text-transform: uppercase;
    }}
    input, textarea {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 0;
      padding: 10px 11px;
      font: inherit;
      color: var(--ink);
      background: var(--field);
    }}
    .website-field {{
      position: absolute;
      left: -10000px;
      width: 1px;
      height: 1px;
      overflow: hidden;
    }}
    textarea {{
      min-height: 110px;
      resize: vertical;
    }}
    button {{
      width: 100%;
      margin-top: 16px;
      border: 1px solid var(--line);
      border-radius: 0;
      padding: 12px 16px;
      background: var(--ink);
      color: white;
      font-family: "Courier New", Courier, monospace;
      font-size: 12px;
      font-weight: 400;
      text-transform: uppercase;
      cursor: pointer;
    }}
    button:hover {{ background: var(--accent); color: var(--ink); }}
    .fineprint {{
      margin-top: 14px;
      font-size: 13px;
      color: var(--muted);
    }}
    .notice {{
      padding: 12px;
      border-radius: 0;
      margin: 0 0 16px;
      font-size: 14px;
    }}
    .notice.error {{
      color: var(--warn);
      background: var(--warn-bg);
      border: 1px solid #fed7aa;
    }}
    .notice.success {{
      color: var(--ok);
      background: var(--ok-bg);
      border: 1px solid #bbf7d0;
    }}
    @media (max-width: 840px) {{
      main {{
        grid-template-columns: 1fr;
        gap: 28px;
        padding: 32px 0;
      }}
      h1 {{ font-size: 48px; }}
      .intro {{ font-size: 16px; }}
    }}
    @media (max-width: 420px) {{
      h1 {{ font-size: 40px; }}
    }}
  </style>
</head>
<body>
  <main>
    <section>
      <div class="eyebrow">Open beta // access request</div>
      <h1>EDA MCP</h1>
      <p class="intro">
        A small, opinionated board-design service for agents: SKiDL in,
        schematic/layout/routing/DRC feedback out. Access is opening gradually
        while usage tracking, quotas, and the manufacturing path are hardened.
      </p>
      <ul class="points">
        <li><span class="mark">01</span><span>Agents submit SKiDL code. The service owns schematic, placement, routing, DRC, and manufacturing artifacts.</span></li>
        <li><span class="mark">02</span><span>Failures come back as structured corrections instead of a pile of opaque CAD logs.</span></li>
        <li><span class="mark">03</span><span>Beta access is personal and metered so the shared routing workers stay usable.</span></li>
      </ul>
    </section>
    <section class="form-panel" aria-label="Open beta signup form">
      <h2>Request pin</h2>
      <p>Tell me who you are and what you want to build. I will approve requests manually and email a personal MCP token.</p>
      {error_html}
      {submitted_html}
      <form method="post" action="/signup">
        <input type="hidden" name="source" value="signup_page">
        <div class="website-field" aria-hidden="true">
          <label for="website">Website</label>
          <input id="website" name="website" type="text" tabindex="-1" autocomplete="off">
        </div>
        <label for="email">Email</label>
        <input id="email" name="email" type="email" value="{_field(values, "email")}" autocomplete="email" required>
        <label for="name">Name</label>
        <input id="name" name="name" type="text" value="{_field(values, "name")}" autocomplete="name">
        <label for="organization">Company or project</label>
        <input id="organization" name="organization" type="text" value="{_field(values, "organization")}">
        <label for="use_case">What are you trying to make?</label>
        <textarea id="use_case" name="use_case" required>{_field(values, "use_case")}</textarea>
        <button type="submit">Request beta access</button>
      </form>
      <p class="fineprint">No automatic ordering, no shared tokens, no public free-for-all. This is a controlled beta for real board-design workflows.</p>
    </section>
  </main>
</body>
</html>"""


async def signup_page(request: Request) -> HTMLResponse:
    return HTMLResponse(_signup_html())


async def signup_thanks(request: Request) -> HTMLResponse:
    return HTMLResponse(_signup_html(submitted=True))


def _admin_login_html(error: str = "") -> str:
    error_html = f'<div class="notice error">{html.escape(error)}</div>' if error else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Admin - EDA-MCP</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: Arial, Helvetica, sans-serif;
      color: #191917;
      background:
        linear-gradient(90deg, rgba(31,31,27,.16) 1px, transparent 1px),
        linear-gradient(rgba(31,31,27,.16) 1px, transparent 1px),
        #e8e5dc;
      background-size: 22px 22px;
    }}
    form {{
      width: min(420px, calc(100vw - 28px));
      border: 1px solid #191917;
      background: #efede6;
      box-shadow: 8px 8px 0 rgba(25,25,23,.16);
      padding: 18px;
    }}
    h1 {{
      margin: 0 0 14px;
      font-family: "Times New Roman", Times, serif;
      font-size: 40px;
      font-weight: 400;
      letter-spacing: 0;
    }}
    label {{
      display: block;
      margin: 0 0 6px;
      font-family: "Courier New", Courier, monospace;
      font-size: 11px;
      text-transform: uppercase;
    }}
    input, button {{
      width: 100%;
      border: 1px solid #191917;
      border-radius: 0;
      padding: 10px 11px;
      font: inherit;
      background: #f8f7f1;
    }}
    button {{
      margin-top: 14px;
      background: #191917;
      color: #fff;
      font-family: "Courier New", Courier, monospace;
      font-size: 12px;
      text-transform: uppercase;
      cursor: pointer;
    }}
    .notice {{
      margin: 0 0 12px;
      padding: 10px;
      border: 1px solid #8f260f;
      background: #f3dfd8;
      color: #8f260f;
      font-size: 14px;
    }}
  </style>
</head>
<body>
  <form method="post" action="/admin/login">
    <h1>Admin</h1>
    {error_html}
    <label for="token">Owner token</label>
    <input id="token" name="token" type="password" autocomplete="current-password" autofocus>
    <button type="submit">Unlock</button>
  </form>
</body>
</html>"""


async def admin_login_page(request: Request) -> HTMLResponse:
    return HTMLResponse(_admin_login_html())


async def admin_login_submit(request: Request) -> HTMLResponse | RedirectResponse:
    if not EDA_AUTH_TOKEN:
        return HTMLResponse(_admin_login_html("Owner token is not configured."), status_code=503)
    if _admin_login_rate_limited(_client_ip(request)):
        return HTMLResponse(
            _admin_login_html("Too many login attempts. Please try again later."),
            status_code=429,
        )
    values = await _request_payload(request)
    token = str(values.get("token", ""))
    if not hmac.compare_digest(token, EDA_AUTH_TOKEN):
        return HTMLResponse(_admin_login_html("Invalid owner token."), status_code=401)

    response = RedirectResponse("/admin/beta-signups", status_code=303)
    response.set_cookie(
        ADMIN_COOKIE,
        _admin_cookie_signature(),
        httponly=True,
        secure=_cookie_secure(request),
        samesite="strict",
        max_age=60 * 60 * 12,
        path="/admin",
    )
    return response


async def admin_logout(request: Request) -> RedirectResponse:
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(ADMIN_COOKIE, path="/admin")
    return response


async def _request_payload(request: Request) -> dict:
    content_length = request.headers.get("content-length")
    try:
        if content_length and int(content_length) > MAX_SIGNUP_BODY_BYTES:
            return {"__too_large": True}
    except ValueError:
        return {"__too_large": True}
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return {}
        return body if isinstance(body, dict) else {}

    raw = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _clean_signup_payload(payload: dict) -> dict:
    return {
        "too_large": bool(payload.get("__too_large")),
        "website": str(payload.get("website", "")).strip()[:500],
        "email": str(payload.get("email", "")).strip(),
        "name": str(payload.get("name", "")).strip()[:200],
        "organization": str(payload.get("organization", "")).strip()[:200],
        "use_case": str(payload.get("use_case", "")).strip()[:4000],
        "source": str(payload.get("source", "")).strip()[:120],
    }


def _client_metadata(request: Request) -> dict:
    return {
        "ip": _client_ip(request),
        "user_agent": request.headers.get("user-agent", "")[:300],
        "referer": request.headers.get("referer", "")[:500],
    }


async def _create_signup(request: Request) -> tuple[dict | None, str | None, dict]:
    values = _clean_signup_payload(await _request_payload(request))
    if values["too_large"]:
        return None, "Signup request is too large.", values
    if values["website"]:
        return {"created": True, "status": "pending", "bot_filtered": True}, None, values
    if not values["email"] or not EMAIL_RE.match(values["email"]):
        return None, "Enter a valid email address.", values
    if not values["use_case"]:
        return None, "Tell me briefly what you want to make.", values
    if _signup_rate_limited(_client_ip(request)):
        return None, "Too many signup attempts from this network. Please try again later.", values
    if db.pool is None:
        return None, "Signup storage is not connected yet. Please try again shortly.", values

    signup = await db.create_beta_signup(
        email=values["email"],
        name=values["name"],
        organization=values["organization"],
        use_case=values["use_case"],
        source=values["source"] or "signup",
        metadata=_client_metadata(request),
    )
    signup["owner_notified"] = await _notify_owner(signup, request) if signup.get("created") else False
    return signup, None, values


def _signup_error_status(error: str) -> int:
    if "not connected" in error:
        return 503
    if "too large" in error:
        return 413
    if "Too many" in error:
        return 429
    return 400


async def signup_submit(request: Request) -> HTMLResponse:
    signup, error, values = await _create_signup(request)
    if error:
        return HTMLResponse(
            _signup_html(values=values, error=error),
            status_code=_signup_error_status(error),
        )
    return HTMLResponse(
        _signup_html(values=values, submitted=True, created=bool(signup.get("created"))),
        status_code=201 if signup.get("created") else 200,
    )


async def signup_api(request: Request) -> JSONResponse:
    signup, error, values = await _create_signup(request)
    if error:
        return JSONResponse(
            {"ok": False, "error": error},
            status_code=_signup_error_status(error),
        )
    if signup.get("bot_filtered"):
        signup = {"created": True, "status": "pending"}
    return JSONResponse(
        {"ok": True, "signup": signup},
        status_code=201 if signup.get("created") else 200,
    )


async def beta_signups(request: Request) -> JSONResponse:
    if db.pool is None:
        return JSONResponse({"error": "db not connected"}, status_code=503)
    limit = min(int(request.query_params.get("limit", "100")), 500)
    return JSONResponse(await db.list_beta_signups(limit))


def _admin_shell(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} - EDA-MCP</title>
  <style>
    :root {{
      --ink: #191917;
      --muted: #69665f;
      --line: #1f1f1b;
      --paper: #e8e5dc;
      --panel: #efede6;
      --field: #f8f7f1;
      --accent: #e94f37;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      color: var(--ink);
      background:
        linear-gradient(90deg, rgba(31,31,27,.16) 1px, transparent 1px),
        linear-gradient(rgba(31,31,27,.16) 1px, transparent 1px),
        var(--paper);
      background-size: 22px 22px;
    }}
    main {{
      width: min(1120px, calc(100vw - 28px));
      margin: 0 auto;
      padding: 32px 0;
    }}
    h1 {{
      margin: 0 0 18px;
      font-family: "Times New Roman", Times, serif;
      font-size: 52px;
      font-weight: 400;
      line-height: .95;
      letter-spacing: 0;
    }}
    .panel {{
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow: 8px 8px 0 rgba(25,25,23,.16);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      border: 1px solid var(--line);
      padding: 10px;
      vertical-align: top;
      text-align: left;
    }}
    th {{
      font-family: "Courier New", Courier, monospace;
      font-size: 11px;
      font-weight: 400;
      text-transform: uppercase;
      background: #d9d6cd;
    }}
    .muted {{ color: var(--muted); }}
    .status {{
      display: inline-block;
      border: 1px solid var(--line);
      padding: 2px 6px;
      font-family: "Courier New", Courier, monospace;
      font-size: 11px;
      text-transform: uppercase;
      background: var(--field);
    }}
    button {{
      border: 1px solid var(--line);
      border-radius: 0;
      padding: 8px 10px;
      background: var(--ink);
      color: #fff;
      font-family: "Courier New", Courier, monospace;
      font-size: 11px;
      text-transform: uppercase;
      cursor: pointer;
    }}
    button:hover {{ background: var(--accent); color: var(--ink); }}
    code {{
      display: block;
      max-width: 100%;
      overflow-wrap: anywhere;
      border: 1px solid var(--line);
      background: var(--field);
      padding: 10px;
      font-family: "Courier New", Courier, monospace;
      font-size: 13px;
    }}
    .token {{ margin-top: 18px; }}
  </style>
</head>
<body>
  <main>
    <h1>{html.escape(title)}</h1>
    {body}
  </main>
</body>
</html>"""


def _signup_table(signups: list[dict]) -> str:
    if not signups:
        return '<div class="panel" style="padding:16px">No beta requests yet.</div>'
    rows = []
    for item in signups:
        approve = ""
        if item["status"] == "pending":
            approve = (
                f'<form method="post" action="/admin/beta-signups/{item["id"]}/approve">'
                '<button type="submit">Approve</button>'
                "</form>"
            )
        rows.append(
            "<tr>"
            f"<td>{item['id']}</td>"
            f"<td>{html.escape(item['email'])}<br><span class=\"muted\">{html.escape(item.get('name') or '')}</span></td>"
            f"<td>{html.escape(item.get('organization') or '')}</td>"
            f"<td>{html.escape(item.get('use_case') or '')}</td>"
            f"<td><span class=\"status\">{html.escape(item['status'])}</span></td>"
            f"<td>{html.escape(item.get('created_at') or '')}</td>"
            f"<td>{approve}</td>"
            "</tr>"
        )
    return (
        '<div class="panel"><table>'
        "<thead><tr>"
        "<th>ID</th><th>Email</th><th>Project</th><th>Use case</th>"
        "<th>Status</th><th>Created</th><th>Action</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


async def admin_beta_signups(request: Request) -> HTMLResponse:
    if db.pool is None:
        return HTMLResponse(_admin_shell("Beta requests", '<div class="panel" style="padding:16px">DB not connected.</div>'), status_code=503)
    limit = min(int(request.query_params.get("limit", "100")), 500)
    body = _signup_table(await db.list_beta_signups(limit))
    return HTMLResponse(_admin_shell("Beta requests", body))


async def _approve_signup(request: Request, signup_id: int) -> dict:
    if db.pool is None:
        raise RuntimeError("db not connected")
    token, prefix, token_hash = _generate_api_token()
    approval = await db.approve_beta_signup(
        signup_id,
        token_prefix=prefix,
        token_hash=token_hash,
        key_name="open-beta",
    )
    approval["token"] = token
    approval["email_sent"] = await _email_approval(approval["signup"], token, request)
    return approval


async def approve_beta_signup(request: Request) -> HTMLResponse:
    signup_id = int(request.path_params["signup_id"])
    try:
        approval = await _approve_signup(request, signup_id)
    except KeyError:
        return HTMLResponse(_admin_shell("Request not found", '<div class="panel" style="padding:16px">No signup exists for that id.</div>'), status_code=404)
    except ValueError as exc:
        return HTMLResponse(_admin_shell("Already handled", f'<div class="panel" style="padding:16px">{html.escape(str(exc))}</div>'), status_code=409)
    except RuntimeError as exc:
        return HTMLResponse(_admin_shell("Approval failed", f'<div class="panel" style="padding:16px">{html.escape(str(exc))}</div>'), status_code=503)

    signup = approval["signup"]
    mcp_url = f"{public_base_url(request)}/mcp"
    email_note = "Approval email sent." if approval["email_sent"] else "Email not sent; copy this token manually."
    body = (
        '<div class="panel" style="padding:16px">'
        f"<p>Approved <strong>{html.escape(signup['email'])}</strong>. {email_note}</p>"
        f'<p class="muted">MCP URL: {html.escape(mcp_url)}</p>'
        '<div class="token"><label>Bearer token - shown once</label>'
        f"<code>{html.escape(approval['token'])}</code></div>"
        '<p><a href="/admin/beta-signups">Back to requests</a></p>'
        "</div>"
    )
    return HTMLResponse(_admin_shell("Approved", body), status_code=201)


async def approve_beta_signup_api(request: Request) -> JSONResponse:
    signup_id = int(request.path_params["signup_id"])
    try:
        approval = await _approve_signup(request, signup_id)
    except KeyError:
        return JSONResponse({"ok": False, "error": "signup not found"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    except RuntimeError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    return JSONResponse({"ok": True, "approval": approval}, status_code=201)


def create_app() -> Starlette:
    mcp_app = mcp.streamable_http_app()

    # Inject our routes and middleware into the FastMCP app so its lifespan
    # (which starts the session manager task group) runs properly.
    mcp_app.routes.insert(0, Route("/", signup_page, methods=["GET"]))
    mcp_app.routes.insert(1, Route("/signup", signup_page, methods=["GET"]))
    mcp_app.routes.insert(2, Route("/signup", signup_submit, methods=["POST"]))
    mcp_app.routes.insert(3, Route("/signup/thanks", signup_thanks, methods=["GET"]))
    mcp_app.routes.insert(4, Route("/api/beta-signup", signup_api, methods=["POST"]))
    mcp_app.routes.insert(5, Route("/beta-signups", beta_signups, methods=["GET"]))
    mcp_app.routes.insert(6, Route("/admin/login", admin_login_page, methods=["GET"]))
    mcp_app.routes.insert(7, Route("/admin/login", admin_login_submit, methods=["POST"]))
    mcp_app.routes.insert(8, Route("/admin/logout", admin_logout, methods=["GET", "POST"]))
    mcp_app.routes.insert(9, Route("/admin", admin_beta_signups, methods=["GET"]))
    mcp_app.routes.insert(10, Route("/admin/beta-signups", admin_beta_signups, methods=["GET"]))
    mcp_app.routes.insert(11, Route("/admin/beta-signups/{signup_id:int}/approve", approve_beta_signup, methods=["POST"]))
    mcp_app.routes.insert(12, Route("/api/beta-signups/{signup_id:int}/approve", approve_beta_signup_api, methods=["POST"]))
    mcp_app.routes.insert(13, Route("/health", health))
    mcp_app.routes.insert(14, Route("/estimates", estimates))
    mcp_app.middleware_stack = None  # force rebuild
    mcp_app.user_middleware.insert(0, Middleware(BearerTokenMiddleware))

    # Wrap the original lifespan to also manage Postgres
    original_lifespan = mcp_app.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(app):
        database_url = os.environ.get("DATABASE_URL")
        if database_url:
            await db.connect(database_url)
            stale = await db.fail_stale_running_jobs()
            print("Postgres connected", flush=True)
            if stale:
                print(f"Marked {stale} stale running job(s) failed", flush=True)
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
