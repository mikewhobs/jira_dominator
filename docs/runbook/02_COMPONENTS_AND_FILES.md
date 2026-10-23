# Components and Files

## Directory Structure

```
jira_dominator/
├── jira_dominator.py          # CORE — JiraDominator class + CLI main()
├── jira_mcp_server.py         # MCP stdio server (17 tools)
├── jira_helper.py             # Thin CLI for quick queries
├── jira_cli.py                # Interactive menu CLI
├── jira_dominator_app.py      # macOS menu-bar app (BROKEN import)
├── run_mcp_server.sh          # Venv bootstrap + MCP launch
├── install_dependencies.sh    # Dependency installer
├── validate_mcp_server.py     # Import/syntax check for MCP module
├── test_mcp_server.py         # MCP integration tests (needs .env)
├── test_mcp_structure.py      # MCP structure tests
├── build_macos_app.py         # macOS .app builder
├── create_app_bundle.py       # App bundle helper
├── requirements.txt           # Python deps (pinned)
├── LICENSE                    # MIT
├── scripts/build_mcp_binary.sh
├── .github/workflows/release.yml  # tag v* → PyInstaller assets on GitHub Releases
├── docs/release-notes.md      # body text for those releases
├── schema.json                # JSON schema for create/update payloads
├── mcp_config.json            # MCP client config template
├── .env.example               # Credential template
├── .tool-versions             # mise: python 3.12.13
├── docs/
│   ├── runbook/               # This runbook
│   ├── release-notes.md       # GitHub Release body
│   └── SCHEMA_AND_FIELDS.md   # Field metadata docs
└── tests/
    └── test_adf_plain.py      # Unit tests (no network)
```

## Core Module: `jira_dominator.py`

### Class: `JiraDominator`

Single source of truth for all Jira operations.

| Method Group | Key Methods | Responsibility |
|-------------|-------------|----------------|
| **Init / auth** | `__init__`, `_auth`, `_api_json` | Load `.env`, create JIRA client (API v3) |
| **Metadata** | `get_fields`, `get_fields_lookup`, `get_createmeta`, `get_project_versions` | Field/version discovery for validation |
| **Search** | `search_issues_direct`, `search_issues_direct_all` | JQL search with token pagination |
| **Read** | `get_issue_details`, `get_issue_comments`, `get_related_issues` | Issue fetch + normalization |
| **ADF** | `adf_to_plain_text`, `coerce_description_to_adf`, `build_adf_description` | Rich text conversion |
| **Create** | `create_issues`, `_build_create_fields`, `_validate_create_payload` | Bulk issue creation |
| **Update** | `update_issues`, `_validate_update_payload` | Bulk field updates |
| **Transitions** | `transition_to_status`, `transition_issue`, `get_issue_transitions` | Workflow walking + screen fields |
| **Sprint** | `_get_sprint_field_id`, `_resolve_sprint_id`, `_move_issue_to_sprint` | Sprint/backlog assignment via Agile API |
| **Worklog / links** | `log_work`, `link_issues` | Time logging, issue linking |
| **Reports** | `generate_markdown` | Markdown report from issue dicts |
| **Migration** | `migrate_issues`, `validate_migration`, `_bulk_move_issues` | Cross-project moves/copies |
| **Versions** | `create_version`, `_prepare_versions_for_migration` | Version create + prefix stripping |

### Module-level helpers (no Jira connection needed)

- `adf_to_plain_text`, `html_to_plain` — text extraction
- `format_jira_exception` — structured errors for MCP
- `normalize_comment_api_dict` — comment shape normalization

### CLI entry (`main()`)

```
python jira_dominator.py report <issue_key>
python jira_dominator.py update <json_file> [--dry-run]
python jira_dominator.py create <json_file> [--dry-run]
python jira_dominator.py migrate <config_file> [--dry-run]
python jira_dominator.py validate-migration <mapping_file>
python jira_dominator.py diagnose
```

## MCP Server: `jira_mcp_server.py`

- Framework: `mcp.server.Server` over stdio
- Global singleton: `dominator = JiraDominator()` (lazy init via `init_dominator()`)
- All tool handlers catch exceptions → `format_jira_exception()` → JSON error response

### MCP Tools

| Tool | Maps To |
|------|---------|
| `jira_test_connection` | `jira.current_user()` |
| `jira_get_issue_details` | `get_issue_details()` |
| `jira_get_issue_comments` | `get_issue_comments()` |
| `jira_search_issues` | `search_issues_direct()` |
| `jira_get_related_issues` | `get_related_issues()` |
| `jira_generate_report` | `get_related_issues()` + `generate_markdown()` |
| `jira_create_issues` | `create_issues()` |
| `jira_update_issues` | `update_issues()` |
| `jira_log_work` | `log_work()` |
| `jira_link_issues` | `link_issues()` |
| `jira_transition_issue` | `transition_to_status()` |
| `jira_get_priority_issues` | JQL per project |
| `jira_migrate_issues` | `migrate_issues()` |
| `jira_get_fields` | `get_fields()` |
| `jira_get_createmeta` | `get_createmeta()` |
| `jira_validate_migration` | `validate_migration()` |

### MCP Config

Template at `mcp_config.json`. Cursor MCP config must point `command`/`args`/`cwd` at this repo. `run_mcp_server.sh` handles venv creation via mise.

## Helper Scripts

| File | Role |
|------|------|
| `jira_helper.py` | `get_issue_details`, `search_issues`, `get_priority_issues`, `test_connection` — JSON stdout |
| `jira_cli.py` | Interactive menu: connection test, priority issues, reports, search |
| `validate_mcp_server.py` | Import check for `jira_mcp_server` module |
| `run_mcp_server.sh` | Creates `.venv`, installs deps, launches MCP server |

## Config and Schema

| File | Purpose |
|------|---------|
| `.env` | `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` (required, gitignored) |
| `schema.json` | JSON Schema for create/update payload shapes |

## Migration Config Shape

```json
{
  "source_project": "SRC",
  "target_project": "DST",
  "jql": "project = SRC AND component = api",
  "mapping_file": "migration_mapping.json",
  "options": {
    "mode": "move",
    "version_prefix_strip": "{target_project} - ",
    "migrate_closed_for_unreleased_versions": true,
    "comment_previous_key": true,
    "bulk_move_target_mandatory_fields": { "versions": [] }
  }
}
```

## State Management

- **No application state** — stateless per request
- **In-memory caches**: `_fields_by_id_cache`, `processed_issues` set (report traversal dedup)
- **Persistent state**: migration mapping JSON files written to disk after migrate runs

## Where to Find Things

| Need | Look Here |
|------|-----------|
| Add a new Jira operation | `JiraDominator` method in `jira_dominator.py` |
| Expose to AI assistants | Add tool in `jira_mcp_server.py` `list_tools()` + handler |
| CLI command | `main()` in `jira_dominator.py` or `jira_helper.py` |
| Custom field IDs | `JiraDominator.CHANGE_NOTES_FIELD` etc., or `get_fields_lookup()` |
| Transition logic | `transition_to_status`, `_pick_next_transition_on_path` |
| ADF conversion | `adf_to_plain_text`, `build_adf_description` |
| Migration rules | `migrate_issues` in `jira_dominator.py` |
