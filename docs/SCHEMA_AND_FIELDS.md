# Schema validation and custom fields

Jira Dominator can pull the necessary information from Jira for schema validation and custom field resolution.

## 1. Field metadata (all fields)

**Purpose:** Resolve custom field IDs (e.g. Epic Link, Change notes) and get field types for validation.

**API:** `GET /rest/api/3/field`

**In code:**
- `dominator.get_fields()` – returns the raw list of field objects.
- `dominator.get_fields_lookup()` – returns `{"by_id": {id: field}, "by_name": {name: field}}`.

**MCP:** `jira_get_fields` (no args).

**Typical response shape (per field):**
- `id` – field ID (e.g. `customfield_10014` for Epic Link).
- `name` – display name (e.g. "Epic Link").
- `custom` – boolean.
- `schema.type` – e.g. `string`, `array`, `option`, `issuelink`, `project`, `user`.
- `schema.system` – for system fields (e.g. `epic-link`).
- `schema.items` – for array types (e.g. `component`).

**Use for:**
- **Custom field IDs:** Find the `id` for "Epic Link", "Change notes", "Delivered in Build", etc., so you can stop hardcoding `customfield_10014`, `customfield_10047`, `customfield_10056`.
- **Schema validation:** Map field type to expected JSON shape (string, `{id}, {key}`, `[{name}]`, etc.) and validate create/update payloads.

---

## 2. Create metadata (per project / issue type)

**Purpose:** Know which fields are required and allowed when creating an issue in a given project and issue type.

**API:** `GET /rest/api/3/issue/createmeta?projectKeys=PROJ&expand=projects.issuetypes.fields`

**In code:**
- `dominator.get_createmeta(project_keys="IS")` – metadata for project(s), with issue types and their fields.

**MCP:** `jira_get_createmeta` with optional `project_keys` (e.g. `"IS,VRD"`).

**Typical response shape:**
- `projects[]` → `issuetypes[]` → `fields` (object keyed by field id).
- Each field entry can include: `required`, `schema`, `allowedValues`, `autoCompleteUrl`, etc.

**Use for:**
- **Schema validation:** For a given `(project, issueType)`, validate that create payloads include all required fields and only use allowed fields and value types.
- **Required vs optional:** Build a JSON schema or validator that marks required fields and checks types from `schema` and `allowedValues`.

---

## 3. Suggested flow

1. **At startup or first use (optional cache):**
   - Call `get_fields()` (or `jira_get_fields`) and build a lookup by name → id (and id → schema) for custom fields you care about (Epic Link, Change notes, etc.).
   - Optionally call `get_createmeta(project_keys)` for the projects you create issues in and cache required/allowed fields per issue type.

2. **Custom field resolution:**
   - Replace hardcoded `customfield_10014` with the id from `by_name["Epic Link"]` (or your instance’s Epic Link name).
   - Same for any other custom fields; support an override in config if the field name differs.

3. **Schema validation (e.g. with jsonschema):**
   - Use `get_fields()` to get types; use `get_createmeta()` to get required/allowed per project+issue type.
   - Build a JSON schema (or a simple validator) that checks:
     - Required fields present.
     - Field types match (string, object with `key`/`id`, array of objects, etc.).
     - For option-type fields, values in `allowedValues` if provided.
   - Run this validation before calling `create_issues` or `update_issues` (and optionally for migration config).

4. **Migration config:**
   - If you add an option like `epic_link_field_id`, you can resolve it once from `get_fields_lookup()["by_name"]["Epic Link"]["id"]` and use it in `_set_epic_link` and elsewhere.

---

## 4. Automated validation and accuracy in dry run

Create and update dry runs now **automatically** validate payloads and attach an **accuracy rating**:

- **create_issues(dry_run=True)**  
  For each issue we fetch createmeta for the project (cached per project), validate the payload against required fields and known field ids, and add to the result:
  - `validation.accuracy` (0–100)
  - `validation.accuracy_rating` (`"high"` / `"medium"` / `"low"`)
  - `validation.valid` (true if no missing required and no errors)
  - `validation.missing_required`, `validation.errors`

- **update_issues(dry_run=True)**  
  We check that every field id being updated exists in Jira (from `get_fields()`), and add the same shape of `validation` with accuracy and `unknown_fields` if any.

So you can run a create or update dry run and get both “what would happen” and “how accurate/valid the payload is” without calling the plan’s optional jsonschema step manually.

---

## 5. CLI (optional)

You can add CLI commands to dump this data to files for inspection or codegen:

- `python jira_dominator.py fields [--output fields.json]`
- `python jira_dominator.py createmeta PROJ [--output createmeta_PROJ.json]`

These would call `get_fields()` and `get_createmeta(project_keys)` and write JSON. Not implemented yet; the MCP tools and Python API are enough to pull the data.
