# Jira Dominator — Master Runbook Index

## What This Project Does

Jira Dominator is a Python toolkit for **programmatic Jira Cloud management**. It wraps the Jira REST API v3 with a single core class (`JiraDominator`) and exposes that capability through:

- A **CLI** for reports, bulk create/update, and cross-project migration
- A **helper script** for quick one-off queries
- An **MCP server** so AI assistants (Cursor, Claude, etc.) can read and mutate Jira issues
- An **interactive menu CLI** for exploratory use
- A **macOS menu-bar app** (intended; currently broken — see `04_CURRENT_STATE.md`)

Primary use cases: bulk issue operations, intelligent workflow transitions, Epic/link traversal reports, and cross-project migration.

## North Star

**Make Jira operations scriptable, bulk-safe, and AI-native** — one codebase that developers and agents can drive without clicking through the Jira UI.

## Runbook Files

| File | Contents |
|------|----------|
| [01_ARCHITECTURE.md](./01_ARCHITECTURE.md) | Stack, system design, data flow, external APIs |
| [02_COMPONENTS_AND_FILES.md](./02_COMPONENTS_AND_FILES.md) | Directory map, module responsibilities, entry points |
| [03_RULES_AND_STANDARDS.md](./03_RULES_AND_STANDARDS.md) | Conventions, error handling, testing, gotchas |
| [04_CURRENT_STATE.md](./04_CURRENT_STATE.md) | What works, what's broken, next steps |

## Related Docs (outside runbook/)

| Path | Purpose |
|------|---------|
| `README.md` | User-facing install/usage guide |
| `MCP_SERVER_README.md` | MCP setup and tool list |
| `INSTALLATION_GUIDE.md` | Detailed install steps |
| `docs/release-notes.md` | GitHub Release body for prebuilt MCP binaries |
| `docs/SCHEMA_AND_FIELDS.md` | Field metadata, createmeta, validation |
| `schema.json` | JSON schema for create/update payloads |
| `.env.example` | Required credential template |

## Quick Start

```bash
# 1. Credentials
cp .env.example .env   # fill in JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN

# 2. Python env (mise + venv)
./install_dependencies.sh   # or: ./run_mcp_server.sh (creates .venv)

# 3. Smoke test
python jira_helper.py test_connection

# 4. MCP (Cursor)
# Point MCP config at jira_mcp_server.py — see mcp_config.json
```

## Credential Requirements

All runtime paths require a `.env` file (gitignored):

```
JIRA_URL=https://your-domain.atlassian.net
JIRA_EMAIL=your-email@example.com
JIRA_API_TOKEN=your-api-token
```

The authenticated user must have **Browse projects** (and write permissions for mutations) on every project touched. Search returns only issues visible to that user.
