# Pending

Status as of 1 November 2026, at `main` commit `609d4c4`. There are no open PRs or
issues, and CI passes.

## Needs a decision

- [ ] **First real server:** what it should do and who will use it. Most of this
      list depends on the answer.
- [ ] **What "a broader group" of users means:** individuals signing up, or
      organizations using company logins. This shapes OAuth. Company logins usually
      mean per-organization permissions, which is a much bigger design.

## Never tested

The first real server should cover the remote host and TLS, and probably real
clients, so they don't need separate testing.

- [ ] A real remote host
- [ ] TLS at the proxy
- [ ] Load, including what the database key lookup adds per request
- [ ] Multiple replicas under real traffic
- [ ] Claude Desktop
- [ ] A real person using the `confirm_action` form. It has only been driven with
      simulated keystrokes, with permission prompts bypassed.
- [ ] Removing the demos other than weather, which CI doesn't cover
- [ ] A Linux bind mount that hasn't been prepared with `chown`. It's expected to
      fail, and the docs say to prepare it first.

## Not built yet

Build these when a real need appears.

- [ ] **OAuth:** the next step once there are outside users. It should mean adding a
      `TokenVerifier` and a provider, plus wiring and testing the discovery route,
      because Myna mounts MCP under `/mcp`. Validate each token's audience.
- [ ] **Key expiry warnings:** database keys stop working after 90 days by default,
      with no warning. A Prometheus metric for keys expiring soon would be a
      reasonable first step.
- [ ] **Per-tool permissions:** every key can use every tool.
- [ ] **Shared rate limits:** limits are per process, so several instances allow more
      in total. The active key cap also isn't strict under simultaneous creates.
- [ ] **Easier removal of the other demos:** `stream_count` and `confirm_action` are
      mixed into `tests/test_wire.py` and `scripts/smoke_test.py`.

## Worth knowing

- **`main` is ahead of `v0.2.0`:** it's nine commits past the tag, including breaking
  changes like the new `MYNA_MCP_API_KEYS` format. The version only changes on request.
- **API docs are open:** `/docs`, `/redoc`, and `/openapi.json` need no key, even in
  production. They describe the REST routes, including the key routes, so restrict
  them on a real deployment.
- **Sensitive secret:** `MYNA_KEY_ADMIN_API_KEY` can create keys that reach every
  tool. Restrict that route at the proxy.
- **SQLite without a volume:** in a container, `myna-migrate` succeeds, but the keys
  are lost with the container.
- **"Bypass permissions" in Claude Code:** a test session started with it on, probably
  from local settings. If so, sessions using that setting skip tool permission
  prompts, so check that's intended.
