# Railway Deployment

Canonical deploy source:

- GitHub repo: `lachlanfysh/eda-mcp`
- Branch: `feat/overnight-product-layer`
- Root directory: repository root
- Railway config: `railway.json`
- Healthcheck: `/health`

Do not deploy from `/home/lachlan/Projects/skidl`. That checkout is the mixed
SKiDL/product workspace and has multiple remotes.

## Preferred Setup

Use Railway's GitHub source integration for normal deploys:

1. Connect the Railway service to `lachlanfysh/eda-mcp`.
2. Set the watched branch to `feat/overnight-product-layer`.
3. Use the repository root and `railway.json`.
4. Keep the MCP service and worker service on the same image, with
   `SERVICE_ROLE=mcp` for the HTTP MCP service and `SERVICE_ROLE=worker` for
   the background worker.

With that in place, pushes to the branch should deploy automatically.

CLI form once Railway auth is working:

```bash
railway service source connect \
  --project "$RAILWAY_PROJECT_ID" \
  --service "$RAILWAY_SERVICE_ID" \
  --environment production \
  --repo lachlanfysh/eda-mcp \
  --branch feat/overnight-product-layer
```

## GitHub Actions Fallback

`.github/workflows/deploy-railway.yml` is a fallback deploy path that runs
`railway up` on relevant pushes and on manual workflow dispatch.

Configure these in GitHub:

- Secret `RAILWAY_TOKEN`: a Railway token valid for CLI deploys.
- Variable `RAILWAY_PROJECT_ID`: the Railway project id.
- Variable `RAILWAY_SERVICE_ID`: the MCP service id or name.
- Variable `RAILWAY_ENVIRONMENT`: optional, defaults to `production`.

The workflow intentionally targets only the canonical repo/branch and uses path
filters matching `railway.json` so documentation-only changes do not redeploy.
