# radiolearn-briefs
Weekly radiology and respiratory medicine research brief — automated curation with multidisciplinary anchors

## Architecture (two execution contexts)

The system has a hybrid design split across two execution contexts, which matters
for how each part talks to GitHub:

- **GitHub Actions** (`fetch_sources.py`, `deliver_outbox.py`) — run on GitHub
  runners and reach `api.github.com` directly via the REST helpers in
  `briefs_lib.py`. No Anthropic proxy involved.
- **Cloud Routine** (editorial curation + brief publication) — runs in the
  proxied agent environment. Because the Anthropic proxy may return `403` for
  `api.github.com`, the routine performs its GitHub I/O with the **GitHub MCP
  tools** (`mcp__github__*`) instead of the REST helpers.

See [`docs/github-mcp-migration.md`](docs/github-mcp-migration.md) for the
REST → MCP mapping and the routine's MCP-based publication procedure.
