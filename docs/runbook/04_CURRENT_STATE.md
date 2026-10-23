# Current State

## What Is Working

### Core Jira Operations
- [x] Jira Cloud auth via `.env` credentials
- [x] Issue read: details, comments, related issues, custom fields
- [x] JQL search with token-based pagination
- [x] Bulk create issues (JSON file or MCP)
- [x] Bulk update issues with field mapping
- [x] Intelligent workflow transitions (path walking, screen field population)
- [x] Work logging and issue linking
- [x] ADF read/write (structured descriptions, comments)
- [x] Markdown report generation with link/subtask/Epic traversal
- [x] Field metadata fetch (`get_fields`, `get_createmeta`)
- [x] Create/update dry-run validation with accuracy ratings

### Migration System
- [x] Cross-project migration (move and copy modes)
- [x] Version prefix stripping and target version creation
- [x] Closed-issue selection rules (unreleased version exceptions)
- [x] Dry-run audit (selection counts, skip reasons, version plan)
- [x] Mapping file persistence and validation

### Entry Points
- [x] `jira_dominator.py` CLI (report, create, update, migrate, validate-migration, diagnose)
- [x] `jira_helper.py` quick CLI
- [x] `jira_cli.py` interactive menu
- [x] `jira_mcp_server.py` MCP server (17 tools)
- [x] `run_mcp_server.sh` venv bootstrap + launch
- [x] `validate_mcp_server.py` import validation

### Tests
- [x] `tests/test_adf_plain.py` — comprehensive unit tests for pure functions (pass when deps installed)

## What Is Broken or Incomplete

### macOS App (`jira_dominator_app.py`)
- **Broken**: imports `from mcp_server import JiraDominatorMCPServer` — `mcp_server.py` does not exist
- PyObjC/AppKit dependencies not in `requirements.txt`
- References Ollama integration — unclear if maintained

### Documentation Drift
- `README.md` MCP tool names don't match implementation (singular vs plural, `jira_list_projects` missing)
- `MCP_SERVER_README.md` has fewer tools documented than server exposes (missing log_work, link_issues, migrate, fields, createmeta, validate_migration)

### Environment
- Default system Python may lack `requirements.txt` deps — must use `.venv` from `run_mcp_server.sh`
- `.tool-versions` pins 3.12.13 but system may run 3.14+ without venv

### No CI
- No automated test runs on push
- Integration tests require live Jira credentials

## Distribution

This repository is the public snapshot. It has no prior history. Release binaries are built by `.github/workflows/release.yml` on tags matching `v*`. Assets: macOS arm64, Linux x64, Windows x64. Credentials stay in the client env or a `.env` beside the frozen executable (`_load_runtime_env`). Process env wins over dotenv files.

The Mac binary is unsigned and not notarized. There is no Intel Mac build.

Release builds use CPython 3.12, matching `.tool-versions`. `scripts/build_mcp_binary.sh` makes its own `build/venv` and must not touch the ambient interpreter. `mcp` stays on 1.30.0 until the server is rewritten for the 2.x `MCPServer` API. PyInstaller collects `mcp.server` and `mcp.shared` only. Collecting the whole `mcp` package imports `mcp.cli`, which needs `typer` from the `mcp[cli]` extra, and the freeze fails.

## Immediate Next Steps

### High Priority
1. **Fix or remove `jira_dominator_app.py`** — update import to `jira_mcp_server` or delete if unused
2. **Sync README/MCP docs** with actual 17 MCP tool names
3. **Add CI** — at minimum `python -m unittest tests.test_adf_plain` on push (no credentials needed)

### Medium Priority
4. **Resolve hardcoded custom field IDs** — load from `get_fields_lookup()` at init with config override
5. **Split `jira_dominator.py`** — extract ADF, transitions, migration into submodules
### Low Priority
6. Wire `schema.json` to runtime jsonschema validation
7. Add `jira_list_projects` MCP tool (documented in older notes but not implemented)
8. PyObjC in requirements if macOS app is kept

## How to Verify Health

```bash
# 1. Venv + deps
./run_mcp_server.sh &   # or install manually
# kill background server after start confirms OK

# 2. Connection
python jira_helper.py test_connection

# 3. Unit tests (no Jira needed)
source .venv/bin/activate
python -m unittest tests.test_adf_plain -v

# 4. MCP module
python validate_mcp_server.py

# 5. Live read (needs .env)
python jira_helper.py get_issue_details PROJ-123
```
