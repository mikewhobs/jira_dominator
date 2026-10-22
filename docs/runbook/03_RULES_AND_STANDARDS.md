# Rules and Standards

## Coding Conventions

- **Python 3.12** — managed via mise (`.tool-versions`)
- **No linter/formatter config** in repo — follow existing style in `jira_dominator.py`
- **Type hints** used on public methods (`Dict`, `List`, `Optional`, `Any`)
- **Shebang**: `#!/usr/bin/env python3` on all executable scripts
- **Imports**: stdlib → third-party → local; no package structure (flat module layout)
- **Naming**: snake_case functions/methods; `JiraDominator` PascalCase class; `jira_*` MCP tool prefix

## Module Boundaries

- **All Jira API logic** belongs in `JiraDominator` — do not duplicate REST calls in entry points
- **Entry points** (`jira_helper.py`, `jira_cli.py`, `jira_mcp_server.py`) are thin adapters only
- **Pure functions** (ADF conversion, error formatting) stay at module level for testability without network

## Error Handling Protocol

### Structured errors for agents

```python
format_jira_exception(exc) → {
    "error": "jira_api_error" | "internal_error",
    "status_code": int | None,
    "message": str,
    "details": dict | str | None
}
```

MCP handlers always catch exceptions and return this shape as JSON text.

### Batch operations

- Each issue in a create/update/migrate batch gets its own result dict
- One failure does **not** abort the batch
- CLI prints `✓` / `○` (dry-run) / `✗` per item

### Skip vs error

- `should_skip_issue(status)` — Done/Cancelled/Canceled issues skipped during recursive traversal (not an error)
- `get_issue_details` returns `None` for skipped issues; MCP reports "not found or skipped"

### Direct REST vs SDK

- Prefer `jira` SDK for standard operations
- Use `requests` + `_api_json` / `_auth` when SDK lacks support (versions, bulk move, search pagination quirks)

## ADF Handling Rules

- **Writes to Jira** must be ADF (`build_adf_description`, `build_adf_comment`, `coerce_description_to_adf`)
- **Reads from Jira** expose three forms when possible:
  - `description_adf` — raw ADF
  - `description_plain` — agent-friendly text
  - `description_rendered` — HTML from `renderedFields`
- Plain strings passed as descriptions are auto-wrapped into ADF paragraphs
- Structured description dicts (`purpose`, `endpoint_details`, `success_criteria`, etc.) render into formatted ADF sections

## Workflow Transition Rules

- Target status matched by **destination status name**, not transition button label
- Walks intermediate states via `_pick_next_transition_on_path` using `DEFAULT_TRANSITION_PATH`
- Transition-screen fields populated only when field ID appears in transition's `fields` map
- Change notes / delivered-in-build deferred if not on screen (applied later or logged as deferred)
- "Submit to QA" → "Ready for Testing" auto-requires worklog (`_transition_requires_worklog`)

## Validation

### Create/update dry-run

- `create_issues(dry_run=True)` and `update_issues(dry_run=True)` validate against `get_createmeta`
- Returns accuracy rating per payload (see `docs/SCHEMA_AND_FIELDS.md`)

### JSON schema

- `schema.json` documents expected create/update payload shapes
- Not enforced at runtime by a validator library — validation is via createmeta + custom `_validate_*` methods

## Testing

### Unit tests (no network)

```bash
# Requires deps installed in active Python
python -m unittest tests.test_adf_plain -v
```

Location: `tests/test_adf_plain.py`

Covers:
- ADF → plain text conversion
- HTML stripping
- `format_jira_exception` shape
- Custom field value simplification (JSM error blobs, ADF in custom fields)
- Markdown report smoke test
- Transition helper logic (pick by target status, worklog, change notes deferral)
- Version merge modes
- Description coercion

### Integration tests (need `.env`)

- `test_mcp_server.py` — MCP server with live Jira
- `test_mcp_structure.py` — MCP module structure
- `validate_mcp_server.py` — import/syntax check

### CI/CD

**None.** No `.github/workflows/`, no pre-commit hooks. Tests are manual.

## Environment Setup

```bash
# Preferred: mise + venv via launch script
./run_mcp_server.sh

# Or manual
mise install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill credentials
```

## Git Conventions

- `.env` gitignored — never commit credentials
- `*_report.md` gitignored (generated output)
- `migration_mapping_*.json` tracked (migration artifacts)
- `mcp_config.json` tracked as template (contains absolute paths — adjust per machine)

## Gotchas and Sharp Edges

### Permissions

- JQL search returns **only issues the API user can see**. 0 results with valid JQL = permission problem, not a bug. Use `diagnose` command to debug.

### Hardcoded custom field IDs

- `customfield_10014` (Epic Link), `customfield_10047` (Change Notes), `customfield_10056` (Delivered in Build) are instance-specific
- Use `get_fields_lookup()` to resolve dynamically; don't assume IDs work on other Jira instances

### Sprint assignment does NOT go through the field payload

- Setting the Sprint via a normal field edit (`fields: {customfield_XXXXX: id}`) is unreliable — the Sprint field is usually not on the create/edit screen, so Jira rejects it ("cannot be set... not on the appropriate screen").
- Correct path (implemented): the **Agile REST API** (`/rest/agile/1.0`), which is screen-independent.
  - Add to sprint: `POST /rest/agile/1.0/sprint/{sprintId}/issue`
  - Send to backlog: `POST /rest/agile/1.0/backlog/issue`
- The `sprint` key in create/update payloads accepts a **sprint id (int)** or an **active/future sprint name** (resolved via the project's boards). `null` / `"backlog"` moves to backlog (update only).
- Sprint field id is resolved dynamically via `_get_sprint_field_id()` (schema custom == `com.pyxis.greenhopper.jira:gh-sprint`), cached. It is only used for discovery, not for writes.
- On **create**, the issue is created first, then moved into the sprint (post-create step) — a failed sprint move does not fail the create; the result message notes it.
- Name resolution only searches **active/future** sprints. Closed sprints are not matched. Kanban boards (no sprint feature) are skipped silently.

### Search pagination

- `search_issues_direct` uses Jira's **token-based** pagination (`nextPageToken`), not `startAt` offset
- MCP `jira_search_issues` documents this; pass `next_page_token` from prior response

### macOS app is broken

- `jira_dominator_app.py` imports `from mcp_server import JiraDominatorMCPServer` — module `mcp_server.py` does not exist
- Should import from `jira_mcp_server` or be refactored
- PyObjC/AppKit not in `requirements.txt`

### README drift

- README lists MCP tools like `jira_create_issue` (singular) — actual tool is `jira_create_issues`
- README lists `jira_list_projects` — not implemented in current MCP server

### `package.json` / openclaw

- Untracked `package.json` + `package-lock.json` with `openclaw` dependency
- Not integrated into Python workflow; purpose unclear

### Instance-specific workflow

- Default transition path and status names assume team's Jira workflow
- Different instances may need path customization

## Technical Debt

1. `jira_dominator.py` is a ~2900-line god class — candidate for splitting (ADF, transitions, migration, search)
2. Hardcoded custom field IDs should migrate to config or dynamic lookup
3. No CI pipeline for unit tests
4. macOS app needs repair or removal
5. `schema.json` not wired to runtime jsonschema validation
6. README/MCP docs out of sync with actual tool names
