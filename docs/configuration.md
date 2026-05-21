# Configuration

All configuration is read from environment variables by `pydantic-settings`.
See [`src/myna/config.py`](../src/myna/config.py) for the source of truth and
[`.env.example`](../.env.example) for a template.

Every variable is prefixed with `MYNA_`. Values from a `.env` file in the
working directory are loaded automatically.

## Reference

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `MYNA_ENV` | `development` \| `staging` \| `production` | `development` | Runtime environment. Controls auto-reload (`development`) and whether the admin API requires `MYNA_ADMIN_API_KEY`. |
| `MYNA_HOST` | string | `0.0.0.0` | Bind address for uvicorn. |
| `MYNA_PORT` | int | `8000` | Bind port for uvicorn. |
| `MYNA_LOG_LEVEL` | string | `INFO` | Log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`). |
| `MYNA_MCP_SERVER_NAME` | string | `myna` | Server name advertised to MCP clients during `initialize`. |
| `MYNA_MCP_MOUNT_PATH` | string | `/mcp` | URL prefix where the MCP Streamable HTTP app is mounted. |
| `MYNA_ADMIN_API_KEY` | string \| null | `null` | Bearer token required for `/api/admin/*` outside development. See [api.md](api.md). |
| `MYNA_MCP_API_KEYS` | JSON object `{token: label}` | `{}` | Bearer tokens accepted on `/mcp` and the human-readable caller label each one resolves to. In development, an empty map allows anonymous access; in staging/production, an empty map blocks all MCP traffic. The label appears in audit logs and the `caller` metric label. Example: `MYNA_MCP_API_KEYS='{"sk-abc":"client-a","sk-def":"client-b"}'`. |

## Notes

- In `MYNA_ENV=development`, the admin API skips auth if no key is set —
  convenient for local work. In `staging`/`production`, requests to
  `/api/admin/*` will return `500` until you set a key.
- `MYNA_MCP_MOUNT_PATH` changes the public endpoint, e.g. set it to
  `/v1/mcp` to expose the server at `https://host/v1/mcp`.
- The same `.env` file is read by `uv run myna` and by the test suite,
  so be careful not to commit secrets into it. Use `.env.example` for
  shared defaults and a local `.env` (gitignored) for secrets.
