# Architecture

## Tech Stack

| Layer | Technology | Version / Notes |
|-------|-----------|-----------------|
| Language | Python | 3.12.13 (`.tool-versions` / mise) |
| Jira client | `jira` | 3.5.1 — wraps REST API; forced to v3 |
| HTTP | `requests` | 2.31.0 — direct REST calls where SDK is insufficient |
| Config | `python-dotenv` | 1.0.0 — loads `.env` at `JiraDominator` init |
| Markdown | `markdown` | 3.5.1 — report rendering |
| MCP | `mcp` | 1.0.0 — stdio MCP server (`jira_mcp_server.py`) |
| System utils | `psutil` | 5.9.6 — macOS app process management |
| macOS GUI | PyObjC / AppKit | `jira_dominator_app.py` only; not in `requirements.txt` |
| Node (ancillary) | `openclaw` | In `package.json`; not wired into Python runtime |

## System Design Pattern

**Monolithic core + thin adapters.** All Jira logic lives in one class (`JiraDominator` in `jira_dominator.py`, ~2900 lines). Entry points are thin wrappers:

```
┌─────────────────┐  ┌──────────────────┐  ┌─────────────────────┐
│  jira_helper.py │  │  jira_cli.py     │  │  jira_mcp_server.py │
│  (argparse CLI) │  │  (interactive)   │  │  (MCP stdio)        │
└────────┬────────┘  └────────┬─────────┘  └──────────┬──────────┘
         │                    │                        │
         └────────────────────┼────────────────────────┘
                              ▼
                    ┌──────────────────┐
                    │  JiraDominator   │
                    │  (jira_dominator)│
                    └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        jira SDK       requests        ADF helpers
        (REST v3)      (direct API)    (plain text)
```

No database, no queue, no persistent state beyond optional migration mapping JSON files on disk.

## Data Flow

### Read Path (issue details → agent/CLI)

1. Entry point receives issue key or JQL
2. `JiraDominator` calls Jira REST API v3 (`jira` SDK or `requests`)
3. Raw fields normalized into a flat dict:
   - ADF description → `description_plain`, `description_adf`, `description_rendered`
   - Custom fields → `custom_fields` map with simplified values
   - Links, subtasks, parent, attachments, comments assembled
4. Done/Cancelled issues may be **skipped** (`should_skip_issue`) on recursive traversal
5. MCP returns JSON `TextContent`; CLI prints JSON or writes markdown report

### Write Path (create / update / transition)

1. Caller supplies JSON payload (file or MCP tool args)
2. Optional dry-run validation via `get_createmeta` + `_validate_create_payload` / `_validate_update_payload`
3. Descriptions coerced to ADF (`coerce_description_to_adf`, `build_adf_description`)
4. Field updates sent via `jira.issue().update()` or direct REST
5. Status changes go through `transition_to_status` — walks workflow path, populates transition-screen fields (change notes, delivered-in-build, worklog)
6. Per-issue result returned (`success` / `dry_run` / `error`); batch ops do not abort on single failure

### Migration Path

1. JSON config: `source_project`, `target_project`, `jql`, `options`, optional `mapping_file`
2. `search_issues_direct_all` fetches candidates (permission-scoped)
3. Selection audit: closed-issue rules, version-based inclusion, component/summary filters
4. Version plan: strip prefixes, create missing target versions
5. Mode `move` (bulk move API) or `copy` (recreate + link)
6. Post-move: version field updates, epic link remapping, issue link recreation
7. Mapping persisted to `migration_mapping_*.json`

## External Dependencies

### Jira Cloud REST API v3

| Endpoint | Used For |
|----------|----------|
| `GET /rest/api/3/field` | Field metadata, custom field ID lookup |
| `GET /rest/api/3/issue/createmeta` | Create payload validation |
| `POST /rest/api/3/search/jql` | JQL search (token-based pagination) |
| `GET/POST /rest/api/3/issue/{key}/comment` | Comments |
| `GET /rest/api/3/issue/{key}/transitions` | Workflow transitions + screen fields |
| `POST /rest/api/3/issue/{key}/transitions` | Execute transition |
| `POST /rest/api/3/issue/{key}/worklog` | Work logging |
| `POST /rest/api/3/issueLink` | Issue linking |
| `GET /rest/api/3/project/{key}/versions` | Version management |
| `POST /rest/api/3/version` | Create versions |
| Bulk move API | Cross-project migration (`_bulk_move_issues`) |

Auth: HTTP Basic (`JIRA_EMAIL` + `JIRA_API_TOKEN`).

### Atlassian Document Format (ADF)

Jira Cloud stores rich text as ADF JSON. The codebase:
- **Writes** ADF via `build_adf_description`, `build_adf_comment`, `coerce_description_to_adf`
- **Reads** ADF via `adf_to_plain_text` for agent-friendly plain text
- **Reads** rendered HTML via Jira's `renderedFields` when available

## Hardcoded Instance Assumptions

These are baked into `JiraDominator` for the team's Jira instance:

| Constant | Field ID | Purpose |
|----------|----------|---------|
| Epic Link | `customfield_10014` | Epic parent linking |
| Change Notes | `customfield_10047` | Required on some transitions |
| Delivered in Build | `customfield_10056` | Build tracking on QA transitions |

`get_fields_lookup()` exists to resolve these dynamically; hardcoded IDs remain as defaults.

## Default Workflow Path (transitions)

When walking to a target status, the code follows this ordered path:

`Open` → `To Do` → `In Progress` → `Ready for Staging` → `Ready for Testing` → `Done`

Transition screen fields are populated only when present on the screen (`_build_transition_fields_for_screen`). Change notes deferred if not on screen.

## Error Model

- `JiraHttpError` — direct REST failures
- `format_jira_exception()` — structured `{error, status_code, message, details}` for MCP/agents
- Batch ops: per-item success/failure; no all-or-nothing transaction
