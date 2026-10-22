#!/usr/bin/env python3

import os
import sys
import json
import time
import re
import requests
from typing import Dict, List, Set, Any, Optional
from jira import JIRA
from jira.exceptions import JIRAError
from dotenv import load_dotenv
from datetime import datetime


DEFAULT_SEARCH_FIELDS = [
    "summary",
    "status",
    "assignee",
    "updated",
    "priority",
    "issuetype",
    "components",
    "fixVersions",
    "versions",
    "description",
    "labels",
    "reporter",
    "created",
]


class JiraHttpError(Exception):
    """HTTP error from direct requests to Jira REST API (non-2xx)."""

    def __init__(
        self,
        status_code: Optional[int],
        message: str,
        details: Any = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.details = details


def format_jira_exception(exc: Exception) -> Dict[str, Any]:
    """Structured error payload for MCP / agents (4xx/5xx, Jira JSON body)."""
    if isinstance(exc, JIRAError):
        details: Any = None
        if exc.response is not None and exc.response.text:
            try:
                details = exc.response.json()
            except Exception:
                details = exc.response.text
        return {
            "error": "jira_api_error",
            "status_code": exc.status_code,
            "message": (exc.text or str(exc)).strip(),
            "details": details,
        }
    if isinstance(exc, JiraHttpError):
        return {
            "error": "jira_api_error",
            "status_code": exc.status_code,
            "message": exc.message,
            "details": exc.details,
        }
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        r = exc.response
        details: Any = None
        if r.text:
            try:
                details = r.json()
            except Exception:
                details = r.text
        return {
            "error": "jira_api_error",
            "status_code": r.status_code,
            "message": r.reason or str(exc),
            "details": details,
        }
    return {
        "error": "internal_error",
        "message": str(exc),
    }


def html_to_plain(html: Optional[str]) -> str:
    if not html or not isinstance(html, str):
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _adf_node_to_plain(node: Any) -> str:
    if not node or not isinstance(node, dict):
        return ""
    ntype = node.get("type")
    if ntype == "text":
        return node.get("text") or ""
    if ntype == "hardBreak":
        return "\n"
    if ntype in ("paragraph", "heading"):
        inner = "".join(_adf_node_to_plain(c) for c in node.get("content") or [])
        if ntype == "heading":
            lvl = (node.get("attrs") or {}).get("level") or 1
            hashes = "#" * min(int(lvl), 6)
            return f"{hashes} {inner}\n" if inner else ""
        return f"{inner}\n" if inner else ""
    if ntype == "bulletList":
        lines = []
        for item in node.get("content") or []:
            t = _adf_node_to_plain(item).strip()
            if t:
                lines.append(f"- {t}")
        return "\n".join(lines) + ("\n" if lines else "")
    if ntype == "orderedList":
        lines = []
        for i, item in enumerate(node.get("content") or [], start=1):
            t = _adf_node_to_plain(item).strip()
            if t:
                lines.append(f"{i}. {t}")
        return "\n".join(lines) + ("\n" if lines else "")
    if ntype == "listItem":
        return "".join(_adf_node_to_plain(c) for c in node.get("content") or "")
    if ntype == "codeBlock":
        inner = "".join(_adf_node_to_plain(c) for c in node.get("content") or [])
        return f"{inner}\n" if inner else ""
    if ntype == "blockquote":
        inner = "".join(_adf_node_to_plain(c) for c in node.get("content") or [])
        return inner
    if ntype in ("mediaGroup", "mediaSingle", "rule"):
        return ""
    if "content" in node:
        return "".join(_adf_node_to_plain(c) for c in node.get("content") or [])
    return ""


def adf_to_plain_text(adf: Any) -> str:
    """Extract plain text from Jira/Confluence ADF document dict."""
    if not adf or not isinstance(adf, dict):
        return ""
    if adf.get("type") == "doc":
        parts = [_adf_node_to_plain(n) for n in adf.get("content") or []]
        return "\n".join(p for p in parts if p).strip()
    return _adf_node_to_plain(adf).strip()


def normalize_comment_api_dict(comment: Dict[str, Any]) -> Dict[str, Any]:
    body_adf = comment.get("body")
    author = comment.get("author") or {}
    plain = ""
    if isinstance(body_adf, dict):
        plain = adf_to_plain_text(body_adf)
    elif isinstance(body_adf, str):
        plain = html_to_plain(body_adf)
    return {
        "id": comment.get("id"),
        "author": {
            "displayName": author.get("displayName"),
            "accountId": author.get("accountId"),
        },
        "created": comment.get("created"),
        "updated": comment.get("updated"),
        "body": plain,
        "body_adf": body_adf if isinstance(body_adf, dict) else None,
    }


def _is_jira_field_error_blob(d: dict) -> bool:
    """
    JSM/Jira returns {errorMessage, i18nErrorMessage} for some custom fields when the API user
    cannot resolve the backing object (e.g. service space). Treat as non-data noise.
    """
    if not d:
        return False
    allowed = frozenset({"errorMessage", "i18nErrorMessage"})
    if not frozenset(d.keys()) <= allowed:
        return False
    return bool(d.get("errorMessage") or d.get("i18nErrorMessage"))


def _format_jira_field_error_blob(d: dict) -> str:
    msg = d.get("errorMessage")
    if isinstance(msg, str) and msg.strip():
        s = msg.strip()
        if len(s) > 240:
            return f"[unavailable] {s[:237]}..."
        return f"[unavailable] {s}"
    return "[unavailable]"


def _is_adf_root_document(d: dict) -> bool:
    """Rich-text custom fields often store ADF with type 'doc' and a content array."""
    return d.get("type") == "doc" and isinstance(d.get("content"), list)


def _load_runtime_env() -> None:
    """Load credentials without overriding variables already set in the process.

    MCP clients inject JIRA_* through the server env block, and that must win.
    A frozen release binary also checks for a .env sitting next to the executable,
    because the process cwd is usually the editor, not the download folder.
    """
    load_dotenv()
    if getattr(sys, "frozen", False):
        load_dotenv(
            os.path.join(os.path.dirname(os.path.abspath(sys.executable)), ".env")
        )


class JiraDominator:
    def __init__(self):
        _load_runtime_env()
        self.jira_url = (os.getenv('JIRA_URL') or '').strip().rstrip('/')
        self.jira_email = (os.getenv('JIRA_EMAIL') or '').strip()
        self.jira_token = (os.getenv('JIRA_API_TOKEN') or '').strip()
        self.jira = JIRA(server=self.jira_url, basic_auth=(self.jira_email, self.jira_token))
        self.jira._options['rest_api_version'] = 3
        self.processed_issues: Set[str] = set()
        self._fields_by_id_cache: Optional[Dict[str, Any]] = None
        self._sprint_field_id_cache: Any = "unset"

    def _resolve_project_key(self, project_ref: str) -> str:
        """
        Resolve a project reference (key or name) to a project key.
        Falls back to the original reference if not resolvable.
        """
        ref = (project_ref or "").strip()
        if not ref:
            return project_ref
        try:
            proj = self.jira.project(ref)
            key = getattr(proj, "key", None)
            if key:
                return str(key)
        except Exception:
            pass
        try:
            for p in self.jira.projects():
                p_key = (getattr(p, "key", None) or "").strip()
                p_name = (getattr(p, "name", None) or "").strip()
                if p_key.lower() == ref.lower() or p_name.lower() == ref.lower():
                    return p_key or ref
        except Exception:
            pass
        return ref

    def get_fields(self) -> List[Dict[str, Any]]:
        """
        Fetch all issue fields from Jira (GET /rest/api/3/field).
        Use for: custom field ID lookup (name -> id), schema types, allowed values.
        Each field has: id, name, custom, schema (type, system, items), etc.
        """
        return self.jira.fields()

    def get_fields_lookup(self) -> Dict[str, Dict[str, Any]]:
        """
        Return a mapping field_id -> field metadata and name -> field metadata
        for schema validation and resolving custom field IDs (e.g. Epic Link).
        """
        fields = self.get_fields()
        by_id = {f["id"]: f for f in fields}
        by_name = {f["name"]: f for f in fields}
        return {"by_id": by_id, "by_name": by_name}

    def get_project_versions(self, project_key: str) -> List[Dict[str, Any]]:
        """
        Fetch all versions for a project (GET /rest/api/3/project/{projectIdOrKey}/versions).
        Returns list of version objects with id, name, archived, released, etc.
        """
        resolved_key = self._resolve_project_key(project_key)
        r = requests.get(
            f"{self.jira_url}/rest/api/3/project/{resolved_key}/versions",
            auth=(self.jira_email, self.jira_token),
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        return r.json()

    def get_project_id(self, project_key: str) -> Optional[str]:
        """Get numeric project ID for a project key (needed for create version API)."""
        try:
            proj = self.jira.project(self._resolve_project_key(project_key))
            pid = getattr(proj, "id", None)
            if pid is not None:
                return str(pid)
            if isinstance(proj, dict):
                return str(proj.get("id", "")) or None
        except Exception:
            pass
        return None

    def create_version(
        self,
        project_key: str,
        name: str,
        description: Optional[str] = None,
        released: bool = False,
        start_date: Optional[str] = None,
        release_date: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Create a version in a project (POST /rest/api/3/version).
        Returns the created version object or None on failure.
        """
        project_id = self.get_project_id(project_key)
        if not project_id:
            print(f"  - Warning: could not resolve project ID for {project_key}; skipping version creation")
            return None
        payload: Dict[str, Any] = {
            "name": name,
            "projectId": int(project_id),
            "released": released,
        }
        if description is not None:
            payload["description"] = description
        if start_date is not None:
            payload["startDate"] = start_date
        if release_date is not None:
            payload["releaseDate"] = release_date
        try:
            r = requests.post(
                f"{self.jira_url}/rest/api/3/version",
                json=payload,
                auth=(self.jira_email, self.jira_token),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"  - Warning: could not create version '{name}' in {project_key}: {e}")
            return None

    def get_createmeta(
        self,
        project_keys: Optional[str] = None,
        expand: str = "projects.issuetypes.fields",
    ) -> Dict[str, Any]:
        """
        Fetch create metadata (GET /rest/api/3/issue/createmeta).
        Use for: required/allowed fields per project and issue type, field schemas
        for validation. Pass project_keys e.g. "PROJ" or "PROJ,OTHER" to filter.
        With expand=projects.issuetypes.fields each issue type includes its fields
        (required, schema, allowedValues, etc.).
        """
        return self.jira.createmeta(projectKeys=project_keys, expand=expand)

    def _build_create_fields(self, issue_data: Dict[str, Any]) -> Dict[str, Any]:
        """Build the fields dict that would be sent to jira.create_issue (for validation or dry-run)."""
        fields = {
            "project": {"key": issue_data["project"]},
            "summary": issue_data["summary"],
            "issuetype": {"name": issue_data.get("type", "Task")},
        }
        if "description" in issue_data:
            fields["description"] = self.coerce_description_to_adf(issue_data["description"])
        if "assignee" in issue_data:
            fields["assignee"] = self._resolve_user_ref(issue_data["assignee"])
        if "reporter" in issue_data:
            fields["reporter"] = self._resolve_user_ref(issue_data["reporter"])
        elif issue_data.get("auto_reporter", True):
            try:
                fields["reporter"] = {"accountId": self.jira.current_user()}
            except Exception:
                pass
        if "priority" in issue_data:
            fields["priority"] = {"name": issue_data["priority"]}
        if "labels" in issue_data:
            fields["labels"] = issue_data["labels"]
        if "components" in issue_data:
            fields["components"] = [{"name": c} for c in issue_data["components"]]
        if "epic_link" in issue_data:
            fields["customfield_10014"] = issue_data["epic_link"]
        if "parent" in issue_data:
            fields["parent"] = {"key": issue_data["parent"]}
        if "fixVersions" in issue_data and issue_data["fixVersions"]:
            fields["fixVersions"] = [{"id": vid} for vid in issue_data["fixVersions"]]
        elif "fix_versions" in issue_data and issue_data["fix_versions"]:
            ids = self._resolve_version_ids(issue_data["project"], issue_data["fix_versions"])
            if ids:
                fields["fixVersions"] = [{"id": vid} for vid in ids]
        elif issue_data.get("default_fix_version"):
            versions = self.get_project_versions(issue_data["project"])
            unreleased = [
                v for v in versions if not v.get("released") and not v.get("archived")
            ]
            if unreleased:
                fields["fixVersions"] = [{"id": str(unreleased[-1]["id"])}]
        if "versions" in issue_data and issue_data["versions"]:
            if isinstance(issue_data["versions"][0], str):
                ids = self._resolve_version_ids(issue_data["project"], issue_data["versions"])
                fields["versions"] = [{"id": vid} for vid in ids]
            else:
                fields["versions"] = [{"id": vid} for vid in issue_data["versions"]]
        elif "affects_versions" in issue_data and issue_data["affects_versions"]:
            ids = self._resolve_version_ids(issue_data["project"], issue_data["affects_versions"])
            if ids:
                fields["versions"] = [{"id": vid} for vid in ids]
        return fields

    def _validate_create_payload(
        self,
        issue_data: Dict[str, Any],
        createmeta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Validate a create payload against Jira createmeta. Returns validation result with
        accuracy (0-100), rating (high/medium/low), missing_required, errors, and details.
        If createmeta is None, fetches it for the issue's project.
        """
        project_key = issue_data.get("project") or ""
        issue_type_name = issue_data.get("type") or "Task"
        built = self._build_create_fields(issue_data)
        if createmeta is None:
            try:
                createmeta = self.get_createmeta(project_keys=project_key)
            except Exception as e:
                return {
                    "valid": False,
                    "accuracy": 0,
                    "accuracy_rating": "unknown",
                    "missing_required": [],
                    "errors": [f"Could not fetch createmeta: {e}"],
                    "details": [],
                }
        missing_required = []
        errors = []
        details = []
        meta_fields = {}
        for proj in createmeta.get("projects") or []:
            if (proj.get("key") or proj.get("id")) != project_key:
                continue
            for it in proj.get("issuetypes") or []:
                if (it.get("name") or "").strip() != issue_type_name.strip():
                    continue
                meta_fields = it.get("fields") or {}
                break
            if meta_fields:
                break
        if not meta_fields:
            return {
                "valid": False,
                "accuracy": 0,
                "accuracy_rating": "low",
                "missing_required": [],
                "errors": [f"Project {project_key} or issue type {issue_type_name} not found in createmeta"],
                "details": [],
            }
        required_ok = 0
        required_total = 0
        for field_id, meta in meta_fields.items():
            if meta.get("required"):
                required_total += 1
                if field_id in built and built[field_id] is not None:
                    required_ok += 1
                    details.append({"field": field_id, "required": True, "present": True})
                else:
                    missing_required.append(meta.get("name") or field_id)
                    details.append({"field": field_id, "required": True, "present": False})
        for field_id, value in built.items():
            if field_id not in meta_fields:
                errors.append(f"Unknown field: {field_id}")
                details.append({"field": field_id, "error": "not in createmeta"})
        required_ok_count = required_ok
        required_total_count = required_total
        type_errors = 0
        if required_total_count == 0:
            required_ratio = 1.0
        else:
            required_ratio = required_ok_count / required_total_count
        score = int(required_ratio * 70)
        if not errors:
            score += 30
        else:
            type_errors = len(errors)
            score = max(0, score - min(30, type_errors * 10))
        score = min(100, score)
        if score >= 90:
            rating = "high"
        elif score >= 60:
            rating = "medium"
        else:
            rating = "low"
        return {
            "valid": len(missing_required) == 0 and len(errors) == 0,
            "accuracy": score,
            "accuracy_rating": rating,
            "missing_required": missing_required,
            "errors": errors,
            "details": details[:20],
        }

    def _validate_update_payload(self, update_field_ids: List[str]) -> Dict[str, Any]:
        """
        Validate that the field ids we intend to update exist in Jira. Returns
        accuracy (0-100), rating, and any unknown fields.
        """
        try:
            lookup = self.get_fields_lookup()
            by_id = lookup.get("by_id") or {}
        except Exception as e:
            return {
                "valid": False,
                "accuracy": 0,
                "accuracy_rating": "unknown",
                "unknown_fields": update_field_ids,
                "errors": [str(e)],
            }
        unknown = [f for f in update_field_ids if f not in by_id]
        if not update_field_ids:
            return {"valid": True, "accuracy": 100, "accuracy_rating": "high", "unknown_fields": [], "errors": []}
        score = max(0, 100 - len(unknown) * 25)
        rating = "high" if score >= 90 else ("medium" if score >= 60 else "low")
        return {
            "valid": len(unknown) == 0,
            "accuracy": score,
            "accuracy_rating": rating,
            "unknown_fields": unknown,
            "errors": [f"Unknown field: {f}" for f in unknown],
        }

    def search_issues_direct(
        self,
        jql: str,
        max_results: int = 50,
        next_page_token: Optional[str] = None,
        fields: Optional[List[str]] = None,
    ):
        """Search a single page using POST /rest/api/3/search/jql (token pagination via nextPageToken)."""
        url = f"{self.jira_url}/rest/api/3/search/jql"
        field_list = list(fields) if fields is not None else list(DEFAULT_SEARCH_FIELDS)
        payload = {
            "jql": jql,
            "maxResults": min(max_results, 500),
            "fields": field_list,
            "fieldsByKeys": False,
        }
        if next_page_token:
            payload["nextPageToken"] = next_page_token
        response = requests.post(
            url,
            json=payload,
            auth=(self.jira_email, self.jira_token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        if response.status_code != 200:
            body: Any = response.text
            try:
                body = response.json()
            except Exception:
                pass
            raise JiraHttpError(response.status_code, f"Search API {response.status_code}", body)
        return response.json()

    def search_issues_direct_all(self, jql: str, page_size: int = 100) -> Dict[str, Any]:
        """
        Search all pages from /rest/api/3/search/jql using nextPageToken.
        Returns normalized shape: {"issues": [...], "total": N, "isLast": True}.
        """
        all_issues: List[Dict[str, Any]] = []
        token: Optional[str] = None
        page_count = 0
        while True:
            page_count += 1
            page = self.search_issues_direct(
                jql=jql,
                max_results=page_size,
                next_page_token=token,
            )
            batch = (
                page.get("issues")
                or page.get("values")
                or (page if isinstance(page, list) else [])
            )
            if not isinstance(batch, list):
                batch = []
            all_issues.extend(batch)
            is_last = bool(page.get("isLast"))
            token = page.get("nextPageToken")
            if is_last or not token:
                break
            if page_count > 200:
                # Safety guard to prevent accidental infinite loops.
                print("Warning: pagination limit reached while searching issues; returning partial results.")
                break
        return {"issues": all_issues, "total": len(all_issues), "isLast": True}

    DEFAULT_WORKFLOW_PATH = [
        "Open", "To Do", "In Progress", "Ready for Staging", "Ready for Testing", "Done", "Canceled",
    ]
    DELIVERED_IN_BUILD_FIELD = "customfield_10056"
    CHANGE_NOTES_FIELD = "customfield_10047"

    def _auth(self) -> tuple:
        return (self.jira_email, self.jira_token)

    def _api_json(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f"{self.jira_url}{path}"
        response = requests.request(
            method,
            url,
            params=params,
            json=payload,
            auth=self._auth(),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        if response.status_code >= 400:
            details: Any = None
            if response.text:
                try:
                    details = response.json()
                except Exception:
                    details = response.text
            raise JiraHttpError(response.status_code, response.reason or "HTTP error", details)
        if response.status_code == 204 or not response.text:
            return None
        return response.json()

    def _normalize_status_name(self, status: str) -> str:
        return (status or "").strip().rstrip(".")

    def _resolve_user_ref(self, user_ref: Optional[str]) -> Dict[str, Any]:
        if user_ref is None or str(user_ref).strip() == "":
            return {"accountId": None}
        ref = str(user_ref).strip()
        if re.match(r"^[a-f0-9]{24,32}$", ref, re.I):
            return {"accountId": ref}
        try:
            users = self.jira.search_users(query=ref, maxResults=1)
            if users:
                account_id = getattr(users[0], "accountId", None)
                if account_id:
                    return {"accountId": account_id}
        except Exception:
            pass
        return {"name": ref}

    def _set_assignee(self, issue_key: str, assignee: Optional[str]) -> None:
        """Assign or unassign via PUT /rest/api/3/issue/{key}/assignee."""
        self._api_json(
            "PUT",
            f"/rest/api/3/issue/{issue_key}/assignee",
            payload=self._resolve_user_ref(assignee),
        )

    def _resolve_version_ids(self, project_key: str, version_refs: List[Any]) -> List[str]:
        """Resolve version names or numeric ids to Jira version id strings."""
        if not version_refs:
            return []
        by_name = {
            (v.get("name") or "").strip().lower(): str(v.get("id"))
            for v in self.get_project_versions(project_key)
            if v.get("name") and v.get("id")
        }
        ids: List[str] = []
        unresolved: List[str] = []
        for ref in version_refs:
            ref_str = str(ref).strip()
            if not ref_str:
                continue
            if ref_str.isdigit():
                ids.append(ref_str)
                continue
            vid = by_name.get(ref_str.lower())
            if vid:
                ids.append(vid)
            else:
                unresolved.append(ref_str)
        if unresolved:
            raise ValueError(
                f"Unknown version name(s) in project {project_key}: {', '.join(unresolved)}"
            )
        return ids

    def _get_sprint_field_id(self) -> Optional[str]:
        """Resolve the instance's Sprint custom field id (schema custom == gh-sprint). Cached."""
        if self._sprint_field_id_cache != "unset":
            return self._sprint_field_id_cache
        field_id: Optional[str] = None
        try:
            for f in self.get_fields():
                schema = f.get("schema") or {}
                if schema.get("custom") == "com.pyxis.greenhopper.jira:gh-sprint":
                    field_id = f.get("id")
                    break
        except Exception:
            field_id = None
        self._sprint_field_id_cache = field_id
        return field_id

    def _resolve_sprint_id(self, project_key: str, sprint_ref: Any) -> Optional[int]:
        """
        Resolve a sprint reference to a numeric sprint id.
        - None / "" / "backlog" -> None (i.e. backlog).
        - numeric (int or digit string) -> that id.
        - name -> matched against active/future sprints on the project's boards.
        Raises ValueError if a name cannot be resolved.
        """
        if sprint_ref is None:
            return None
        ref = str(sprint_ref).strip()
        if not ref or ref.lower() == "backlog":
            return None
        if ref.isdigit():
            return int(ref)
        boards: List[Dict[str, Any]] = []
        try:
            resp = self._api_json(
                "GET", "/rest/agile/1.0/board", params={"projectKeyOrId": project_key}
            )
            boards = (resp or {}).get("values") or []
        except Exception:
            boards = []
        for board in boards:
            board_id = board.get("id")
            if board_id is None:
                continue
            start = 0
            while True:
                try:
                    sresp = self._api_json(
                        "GET",
                        f"/rest/agile/1.0/board/{board_id}/sprint",
                        params={"state": "active,future", "startAt": start, "maxResults": 50},
                    )
                except Exception:
                    # Kanban boards (and boards without a sprint feature) return 400; skip them.
                    break
                values = (sresp or {}).get("values") or []
                for sp in values:
                    if (sp.get("name") or "").strip().lower() == ref.lower():
                        return int(sp.get("id"))
                if (sresp or {}).get("isLast", True) or not values:
                    break
                start += len(values)
        raise ValueError(
            f"Sprint not found by name '{ref}' in active/future sprints for project {project_key}"
        )

    def _move_issue_to_sprint(
        self, issue_key: str, sprint_ref: Any, project_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Move an existing issue into a sprint (or the backlog) via the Agile API.
        This is screen-independent, unlike editing the Sprint field directly.
        """
        pk = project_key or issue_key.rsplit("-", 1)[0]
        sprint_id = self._resolve_sprint_id(pk, sprint_ref)
        if sprint_id is None:
            self._api_json(
                "POST", "/rest/agile/1.0/backlog/issue", payload={"issues": [issue_key]}
            )
            return {"sprint": None, "moved_to": "backlog"}
        self._api_json(
            "POST",
            f"/rest/agile/1.0/sprint/{sprint_id}/issue",
            payload={"issues": [issue_key]},
        )
        return {"sprint": sprint_id, "moved_to": "sprint"}

    def _issue_project_key(self, issue: Any, issue_key: str) -> str:
        try:
            proj = issue.fields.project
            key = getattr(proj, "key", None)
            if key:
                return str(key)
        except Exception:
            pass
        return issue_key.rsplit("-", 1)[0]

    def _existing_version_ids(self, issue: Any, jira_field: str) -> List[str]:
        attr = "fixVersions" if jira_field == "fixVersions" else "versions"
        vals = getattr(issue.fields, attr, None) or []
        out: List[str] = []
        for v in vals:
            vid = getattr(v, "id", None)
            if vid is not None:
                out.append(str(vid))
        return out

    def _merge_version_ids(self, existing: List[str], target: List[str], mode: str) -> List[str]:
        mode = (mode or "set").lower()
        existing_ordered = list(dict.fromkeys(existing))
        target_ordered = list(dict.fromkeys(target))
        target_set = set(target_ordered)
        if mode == "set":
            return target_ordered
        if mode == "add":
            merged = existing_ordered[:]
            for vid in target_ordered:
                if vid not in merged:
                    merged.append(vid)
            return merged
        if mode == "remove":
            return [vid for vid in existing_ordered if vid not in target_set]
        raise ValueError(f"Unknown version mode '{mode}'; use set, add, or remove")

    def _build_version_update_fields(
        self,
        issue: Any,
        issue_key: str,
        fields_to_update: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Build fixVersions / affects versions (versions) payload for issue update."""
        payload: Dict[str, Any] = {}
        audit: Dict[str, Any] = {}
        project_key = fields_to_update.get("project") or self._issue_project_key(issue, issue_key)

        if "fix_versions" in fields_to_update:
            mode = fields_to_update.get("fix_versions_mode", "set")
            refs = fields_to_update["fix_versions"]
            if not isinstance(refs, list):
                raise ValueError("fix_versions must be a list of version names or ids")
            target_ids = self._resolve_version_ids(project_key, refs)
            merged = self._merge_version_ids(
                self._existing_version_ids(issue, "fixVersions"), target_ids, mode
            )
            payload["fixVersions"] = [{"id": vid} for vid in merged]
            audit["fix_versions"] = {
                "mode": mode,
                "project": project_key,
                "requested": refs,
                "resolved_ids": target_ids,
                "result_ids": merged,
            }
        elif "fixVersions" in fields_to_update:
            mode = fields_to_update.get("fix_versions_mode", "set")
            refs = fields_to_update["fixVersions"]
            target_ids = [str(r) for r in refs]
            merged = self._merge_version_ids(
                self._existing_version_ids(issue, "fixVersions"), target_ids, mode
            )
            payload["fixVersions"] = [{"id": vid} for vid in merged]
            audit["fixVersions"] = {"mode": mode, "result_ids": merged}

        if "affects_versions" in fields_to_update:
            mode = fields_to_update.get("affects_versions_mode", "set")
            refs = fields_to_update["affects_versions"]
            if not isinstance(refs, list):
                raise ValueError("affects_versions must be a list of version names or ids")
            target_ids = self._resolve_version_ids(project_key, refs)
            merged = self._merge_version_ids(
                self._existing_version_ids(issue, "versions"), target_ids, mode
            )
            payload["versions"] = [{"id": vid} for vid in merged]
            audit["affects_versions"] = {
                "mode": mode,
                "project": project_key,
                "requested": refs,
                "resolved_ids": target_ids,
                "result_ids": merged,
            }
        elif "versions" in fields_to_update:
            mode = fields_to_update.get("affects_versions_mode", "set")
            refs = fields_to_update["versions"]
            target_ids = [str(r) for r in refs]
            merged = self._merge_version_ids(
                self._existing_version_ids(issue, "versions"), target_ids, mode
            )
            payload["versions"] = [{"id": vid} for vid in merged]
            audit["versions"] = {"mode": mode, "result_ids": merged}

        return payload, audit

    def get_issue_transitions(self, issue_key: str, expand_fields: bool = True) -> List[Dict[str, Any]]:
        """GET /rest/api/3/issue/{key}/transitions (optionally with transitions.fields)."""
        params = {"expand": "transitions.fields"} if expand_fields else None
        data = self._api_json("GET", f"/rest/api/3/issue/{issue_key}/transitions", params=params)
        return (data or {}).get("transitions") or []

    def _transition_matches_target(self, transition: Dict[str, Any], target_status: str) -> bool:
        to_name = self._normalize_status_name((transition.get("to") or {}).get("name", ""))
        return to_name.lower() == self._normalize_status_name(target_status).lower()

    def _pick_transition_to_status(
        self, transitions: List[Dict[str, Any]], target_status: str
    ) -> Optional[Dict[str, Any]]:
        target = self._normalize_status_name(target_status).lower()
        for transition in transitions:
            to_name = self._normalize_status_name(self._transition_target_name(transition))
            if to_name.lower() == target:
                return transition
        return None

    def _pick_next_transition_on_path(
        self,
        transitions: List[Dict[str, Any]],
        current_status: str,
        target_status: str,
        path: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Pick the next transition by target status (to.name), never by transition display name."""
        path = path or self.DEFAULT_WORKFLOW_PATH
        current = self._normalize_status_name(current_status)
        target = self._normalize_status_name(target_status)
        if current.lower() == target.lower():
            return None
        direct = self._pick_transition_to_status(transitions, target)
        if direct:
            return direct
        try:
            current_index = path.index(current)
        except ValueError:
            return None
        for i in range(current_index + 1, len(path)):
            hop = path[i]
            for transition in transitions:
                to_name = self._normalize_status_name(self._transition_target_name(transition))
                if to_name.lower() == hop.lower():
                    return transition
            if hop.lower() == target.lower():
                break
        return None

    def _transition_target_name(self, transition: Dict[str, Any]) -> str:
        to_val = transition.get("to")
        if isinstance(to_val, dict):
            return str(to_val.get("name") or "")
        if isinstance(to_val, str):
            return to_val
        return ""

    def _transition_screen_field_map(self, transition: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        raw = transition.get("fields")
        if not isinstance(raw, dict):
            return {}
        return {fid: meta for fid, meta in raw.items() if isinstance(meta, dict)}

    def _field_on_transition_screen(self, transition: Dict[str, Any], field_id: str) -> bool:
        return field_id in self._transition_screen_field_map(transition)

    def _normalize_worklog_input(self, worklog: Any) -> Optional[Dict[str, Any]]:
        if worklog is None:
            return None
        if isinstance(worklog, str):
            ts = worklog.strip()
            return {"time_spent": ts} if ts else None
        if isinstance(worklog, dict):
            ts = worklog.get("time_spent") or worklog.get("timeSpent")
            if not ts:
                return None
            return {
                "time_spent": str(ts),
                "comment": worklog.get("comment"),
                "started": worklog.get("started"),
            }
        raise ValueError("worklog must be a duration string or object with time_spent")

    def _transition_requires_worklog(self, transition: Dict[str, Any]) -> bool:
        screen = self._transition_screen_field_map(transition)
        for field_id, meta in screen.items():
            schema = meta.get("schema") or {}
            if schema.get("type") == "worklog" or field_id == "worklog":
                return True
            name = (meta.get("name") or "").lower()
            if "log work" in name or name == "worklog":
                return True
        transition_name = (transition.get("name") or "").lower()
        target = self._normalize_status_name(self._transition_target_name(transition)).lower()
        if "submit to qa" in transition_name or target == "ready for testing":
            return True
        return False

    def _build_transition_fields_for_screen(
        self,
        transition: Dict[str, Any],
        *,
        change_notes: Optional[str] = None,
        delivered_in_build: Optional[str] = None,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> tuple[Dict[str, Any], Optional[str]]:
        """Build transition fields payload using only fields present on the transition screen."""
        screen = self._transition_screen_field_map(transition)
        fields_payload: Dict[str, Any] = dict(extra_fields or {})
        deferred_comment: Optional[str] = None

        if change_notes:
            if self.CHANGE_NOTES_FIELD in screen:
                fields_payload[self.CHANGE_NOTES_FIELD] = self.build_adf_comment(change_notes)
            else:
                deferred_comment = change_notes

        if delivered_in_build is not None:
            if self.DELIVERED_IN_BUILD_FIELD in screen:
                fields_payload[self.DELIVERED_IN_BUILD_FIELD] = delivered_in_build
        elif self.DELIVERED_IN_BUILD_FIELD in screen:
            target = self._normalize_status_name(self._transition_target_name(transition))
            if target == "Ready for Testing":
                fields_payload.setdefault(self.DELIVERED_IN_BUILD_FIELD, "ideal")

        for field_id, meta in screen.items():
            if field_id in fields_payload:
                continue
            schema = meta.get("schema") or {}
            if schema.get("type") == "worklog" or field_id == "worklog":
                continue
            if not meta.get("required"):
                continue
            if field_id == self.CHANGE_NOTES_FIELD and change_notes:
                continue
            if field_id == self.DELIVERED_IN_BUILD_FIELD and delivered_in_build is not None:
                continue
            if schema.get("type") == "array":
                fields_payload.setdefault(field_id, [])
            elif schema.get("type") == "string":
                fields_payload.setdefault(field_id, "")

        return fields_payload, deferred_comment

    def _build_worklog_add(
        self,
        time_spent: str,
        comment: Optional[str] = None,
        *,
        plain_comment: bool = False,
    ) -> Dict[str, Any]:
        entry: Dict[str, Any] = {"timeSpent": time_spent}
        if comment:
            entry["comment"] = comment if plain_comment else self.build_adf_comment(comment)
        return {"add": entry}

    def _build_transition_payload(
        self,
        transition_id: str,
        fields: Optional[Dict[str, Any]] = None,
        worklog: Optional[Dict[str, Any]] = None,
        *,
        transition_worklog: bool = False,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"transition": {"id": str(transition_id)}}
        if fields:
            payload["fields"] = fields
        normalized = self._normalize_worklog_input(worklog)
        if normalized and normalized.get("time_spent"):
            payload["update"] = {
                "worklog": [
                    self._build_worklog_add(
                        normalized["time_spent"],
                        normalized.get("comment"),
                        plain_comment=transition_worklog,
                    )
                ]
            }
        return payload

    def _execute_transition(
        self,
        issue_key: str,
        transition: Dict[str, Any],
        *,
        change_notes: Optional[str] = None,
        delivered_in_build: Optional[str] = None,
        worklog: Optional[Any] = None,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        fields_payload, deferred_comment = self._build_transition_fields_for_screen(
            transition,
            change_notes=change_notes,
            delivered_in_build=delivered_in_build,
            extra_fields=extra_fields,
        )
        normalized_worklog = self._normalize_worklog_input(worklog)
        requires_worklog = self._transition_requires_worklog(transition)
        if normalized_worklog is None and requires_worklog:
            note_for_worklog = change_notes or deferred_comment or "Workflow transition via Jira Dominator"
            normalized_worklog = {"time_spent": "15m", "comment": note_for_worklog}
        payload = self._build_transition_payload(
            transition["id"],
            fields=fields_payload or None,
            worklog=normalized_worklog,
            transition_worklog=True,
        )
        self._api_json("POST", f"/rest/api/3/issue/{issue_key}/transitions", payload=payload)
        if deferred_comment:
            self.jira.add_comment(issue_key, body=self.build_adf_comment(deferred_comment))
        return {
            "transition_id": transition["id"],
            "transition_name": transition.get("name"),
            "to_status": self._transition_target_name(transition),
            "fields_sent": list(fields_payload.keys()),
            "worklog_sent": bool(normalized_worklog),
            "comment_fallback": deferred_comment,
        }

    def log_work(
        self,
        issue_key: str,
        time_spent: str,
        comment: Optional[str] = None,
        started: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """POST /rest/api/3/issue/{key}/worklog."""
        payload: Dict[str, Any] = {"timeSpent": time_spent}
        if comment:
            payload["comment"] = self.build_adf_comment(comment)
        if started:
            payload["started"] = started
        if dry_run:
            return {"issue_key": issue_key, "status": "dry_run", "payload": payload}
        result = self._api_json("POST", f"/rest/api/3/issue/{issue_key}/worklog", payload=payload)
        return {"issue_key": issue_key, "status": "success", "worklog": result}

    def link_issues(
        self,
        inward_key: str,
        outward_key: str,
        link_type: str = "Relates",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Create an issue link (Relates, Blocks, etc.)."""
        if dry_run:
            return {
                "status": "dry_run",
                "inward": inward_key,
                "outward": outward_key,
                "link_type": link_type,
            }
        self._create_issue_link(inward_key, outward_key, link_type)
        return {
            "status": "success",
            "inward": inward_key,
            "outward": outward_key,
            "link_type": link_type,
        }

    def transition_to_status(
        self,
        issue_key: str,
        target_status: str,
        *,
        change_notes: Optional[str] = None,
        delivered_in_build: Optional[str] = None,
        worklog: Optional[Dict[str, Any]] = None,
        max_steps: int = 10,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Walk an issue to target_status using available transitions (matched by to.name).
        Bundles required transition fields and optional worklog on the same transition call.
        """
        target_status = self._normalize_status_name(target_status)
        issue = self.jira.issue(issue_key)
        steps: List[Dict[str, Any]] = []
        simulated_current = issue.fields.status.name
        for _ in range(max_steps):
            if self._normalize_status_name(simulated_current).lower() == target_status.lower():
                break
            transitions = self.get_issue_transitions(issue_key) if not dry_run else self.get_issue_transitions(issue_key)
            next_transition = self._pick_next_transition_on_path(
                transitions, simulated_current, target_status
            )
            if not next_transition:
                return {
                    "issue_key": issue_key,
                    "status": "error",
                    "message": f"No transition path from '{simulated_current}' to '{target_status}'",
                    "steps": steps,
                    "final_status": simulated_current,
                }
            to_status = self._transition_target_name(next_transition)
            step_info = {
                "from_status": simulated_current,
                "transition_name": next_transition.get("name"),
                "to_status": to_status,
            }
            if dry_run:
                steps.append(step_info)
                simulated_current = to_status or simulated_current
                continue
            step_worklog = worklog if self._transition_requires_worklog(next_transition) else None
            executed = self._execute_transition(
                issue_key,
                next_transition,
                change_notes=change_notes,
                delivered_in_build=delivered_in_build,
                worklog=step_worklog,
            )
            step_info.update(executed)
            steps.append(step_info)
            issue = self.jira.issue(issue_key)
            simulated_current = issue.fields.status.name
            if self._normalize_status_name(simulated_current).lower() == target_status.lower():
                break
        final_status = simulated_current if dry_run else issue.fields.status.name
        ok = self._normalize_status_name(final_status).lower() == target_status.lower()
        return {
            "issue_key": issue_key,
            "status": "dry_run" if dry_run else ("success" if ok else "partial"),
            "final_status": final_status,
            "target_status": target_status,
            "steps": steps,
        }

    def coerce_description_to_adf(self, description: Any) -> dict:
        """Accept ADF doc, markdown-ish string, or partial structured description."""
        if isinstance(description, dict):
            if description.get("type") == "doc":
                return description
            return self.build_adf_description(
                purpose=description.get("purpose") or "",
                endpoint_details=description.get("endpoint_details") or {},
                success_criteria=description.get("success_criteria") or [],
                failure_conditions=description.get("failure_conditions") or [],
                acceptance_criteria=description.get("acceptance_criteria") or [],
            )
        if isinstance(description, str):
            return self.build_adf_comment(description)
        raise ValueError("description must be a string, ADF doc, or structured object")

    def build_adf_description(self, purpose: str, endpoint_details: dict, success_criteria: list, failure_conditions: list, acceptance_criteria: list) -> dict:
        """
        Builds a Jira Atlassian Document Format (ADF) JSON object from structured content.
        """
        def text_node(text, is_code=False):
            node = {"type": "text", "text": str(text)}
            if is_code:
                node["marks"] = [{"type": "code"}]
            return node

        def paragraph_node(content_nodes):
            return {"type": "paragraph", "content": content_nodes}

        def list_item_node(content_nodes):
            return {"type": "listItem", "content": [paragraph_node(content_nodes)]}
        
        def heading_node(text, level=2):
            return {"type": "heading", "attrs": {"level": level}, "content": [text_node(text)]}

        content = [
            heading_node("Purpose"),
            paragraph_node([text_node(purpose)]),
            heading_node("Endpoint Details"),
            {
                "type": "bulletList",
                "content": [
                    list_item_node([
                        text_node("Method: "),
                        text_node(endpoint_details.get('method', ''), is_code=True)
                    ]),
                    list_item_node([
                        text_node("Path: "),
                        text_node(endpoint_details.get('path', ''), is_code=True)
                    ])
                ]
            },
            heading_node("Expected Behavior (Success Criteria)"),
            {
                "type": "orderedList",
                "content": [list_item_node([text_node(item)]) for item in success_criteria]
            },
            heading_node("Failure Conditions (Error Scenarios)"),
            {
                "type": "orderedList",
                "content": [list_item_node([text_node(item)]) for item in failure_conditions]
            },
            heading_node("Pass/Fail Metrics (Acceptance Criteria)"),
            {
                "type": "bulletList",
                "content": [list_item_node([text_node(item)]) for item in acceptance_criteria]
            }
        ]
        return {"version": 1, "type": "doc", "content": content}

    def build_adf_comment(self, text: str) -> dict:
        """Builds a simple ADF object for a comment string."""
        return {
            "version": 1,
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": text
                        }
                    ]
                }
            ]
        }

    def should_skip_issue(self, status: str) -> bool:
        """Determine if an issue should be skipped based on its status."""
        status = status.lower()
        return status in ['canceled', 'cancelled', 'done']

    def _is_done_status(self, status_obj: Any) -> bool:
        """
        Prefer Jira status category to determine closed/done state.
        Falls back to status name check when category is unavailable.
        """
        if not status_obj:
            return False
        if isinstance(status_obj, dict):
            cat = status_obj.get("statusCategory") or {}
            cat_key = (cat.get("key") or "").strip().lower()
            if cat_key == "done":
                return True
            status_name = (status_obj.get("name") or "").strip()
            return self.should_skip_issue(status_name) if status_name else False
        cat = getattr(status_obj, "statusCategory", None)
        cat_key = (getattr(cat, "key", "") or "").strip().lower() if cat else ""
        if cat_key == "done":
            return True
        status_name = (getattr(status_obj, "name", "") or "").strip()
        return self.should_skip_issue(status_name) if status_name else False

    def _version_is_unreleased(
        self,
        version_obj: Dict[str, Any],
        source_unreleased_by_name: Optional[Set[str]] = None,
    ) -> bool:
        """Determine if a version should be treated as unreleased."""
        released = version_obj.get("released")
        if released is False:
            return True
        if released is True:
            return False
        name = (version_obj.get("name") or "").strip()
        return bool(name and source_unreleased_by_name and name in source_unreleased_by_name)

    def _get_fields_by_id(self) -> Dict[str, Dict[str, Any]]:
        if self._fields_by_id_cache is None:
            self._fields_by_id_cache = self.get_fields_lookup().get("by_id") or {}
        return self._fields_by_id_cache

    def _simplify_field_value(self, val: Any, depth: int = 0) -> Any:
        if val is None or depth > 14:
            return val
        if isinstance(val, (str, int, float, bool)):
            return val
        if isinstance(val, list):
            return [self._simplify_field_value(x, depth + 1) for x in val]
        if hasattr(val, "key") and hasattr(val, "fields"):
            try:
                return f"{val.key}: {val.fields.summary}"
            except Exception:
                return str(getattr(val, "key", val))
        if isinstance(val, dict):
            if _is_jira_field_error_blob(val):
                return _format_jira_field_error_blob(val)
            if _is_adf_root_document(val):
                return adf_to_plain_text(val) or ""
            if val.get("displayName"):
                return val.get("displayName")
            if val.get("key") and isinstance(val.get("fields"), dict):
                summ = val["fields"].get("summary")
                return f"{val['key']}: {summ}" if summ else val["key"]
            if val.get("key") and "fields" not in val:
                return val.get("key")
            if val.get("name") is not None and len(val) <= 8:
                if "value" not in val or len(val) <= 3:
                    return val.get("name")
            if "value" in val and len(val) <= 5:
                inner = val.get("value")
                if inner is not None and not isinstance(inner, (dict, list)):
                    return inner
            return {k: self._simplify_field_value(v, depth + 1) for k, v in val.items()}
        if hasattr(val, "raw") and isinstance(getattr(val, "raw", None), dict):
            return self._simplify_field_value(val.raw, depth + 1)
        return str(val)

    def _parent_payload(self, fields_raw: Dict[str, Any]) -> Optional[Dict[str, Optional[str]]]:
        p = fields_raw.get("parent")
        if not p:
            return None
        if isinstance(p, dict):
            inner = p.get("fields") if isinstance(p.get("fields"), dict) else {}
            return {
                "key": p.get("key"),
                "summary": inner.get("summary"),
            }
        return None

    def _attachments_payload(self, fields_raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        atts = fields_raw.get("attachment") or []
        if not isinstance(atts, list):
            return []
        out: List[Dict[str, Any]] = []
        for a in atts:
            if not isinstance(a, dict):
                continue
            out.append({
                "filename": a.get("filename"),
                "size": a.get("size"),
                "mimeType": a.get("mimeType"),
                "created": a.get("created"),
                "content": a.get("content"),
            })
        return out

    def _issuelinks_payload(self, issue: Any, fields_raw: Dict[str, Any]) -> List[Dict[str, Optional[str]]]:
        raw_links = fields_raw.get("issuelinks")
        if isinstance(raw_links, list) and raw_links and isinstance(raw_links[0], dict):
            out: List[Dict[str, Optional[str]]] = []
            for link in raw_links:
                t = link.get("type")
                typ = (t.get("name") if isinstance(t, dict) else str(t)) if t else "Link"
                inn = link.get("inwardIssue") or {}
                outw = link.get("outwardIssue") or {}
                out.append({
                    "type": typ or "Link",
                    "inward": inn.get("key") if isinstance(inn, dict) else None,
                    "outward": outw.get("key") if isinstance(outw, dict) else None,
                })
            return out
        links = getattr(issue.fields, "issuelinks", None) or []
        return [
            {
                "type": str(link.type.name),
                "inward": str(link.inwardIssue.key) if getattr(link, "inwardIssue", None) else None,
                "outward": str(link.outwardIssue.key) if getattr(link, "outwardIssue", None) else None,
            }
            for link in links
        ]

    def _subtasks_payload(self, issue: Any, fields_raw: Dict[str, Any]) -> List[Dict[str, str]]:
        raw_st = fields_raw.get("subtasks")
        if isinstance(raw_st, list) and raw_st and isinstance(raw_st[0], dict):
            out: List[Dict[str, str]] = []
            for st in raw_st:
                if not isinstance(st, dict):
                    continue
                key = st.get("key")
                if not key:
                    continue
                fields = st.get("fields") if isinstance(st.get("fields"), dict) else {}
                summ = fields.get("summary") or ""
                status_obj = fields.get("status") or {}
                st_name = status_obj.get("name") if isinstance(status_obj, dict) else None
                if st_name and self.should_skip_issue(st_name):
                    continue
                out.append({"key": str(key), "summary": str(summ)})
            return out
        out: List[Dict[str, str]] = []
        for subtask in issue.fields.subtasks or []:
            if self.should_skip_issue(subtask.fields.status.name):
                continue
            out.append({"key": str(subtask.key), "summary": str(subtask.fields.summary)})
        return out

    def _build_custom_fields_map(
        self,
        fields_raw: Dict[str, Any],
        extra_fields: Optional[List[str]],
    ) -> Dict[str, Dict[str, Any]]:
        extra_set = set(extra_fields or [])
        by_id = self._get_fields_by_id()
        result: Dict[str, Dict[str, Any]] = {}
        for fid, val in fields_raw.items():
            if val is None:
                continue
            if not (fid.startswith("customfield_") or fid in extra_set):
                continue
            meta = by_id.get(fid) or {}
            name = meta.get("name") or fid
            try:
                simplified = self._simplify_field_value(val)
                json.dumps(simplified)
            except TypeError:
                simplified = str(val)
            result[fid] = {"name": name, "value": simplified}
        return result

    def get_issue_comments(
        self,
        issue_key: str,
        start_at: int = 0,
        max_results: int = 50,
    ) -> Dict[str, Any]:
        """GET /rest/api/3/issue/{issueIdOrKey}/comment (ordered, paginated)."""
        url = f"{self.jira_url}/rest/api/3/issue/{issue_key}/comment"
        r = requests.get(
            url,
            params={"startAt": max(0, start_at), "maxResults": min(max(max_results, 1), 500)},
            auth=(self.jira_email, self.jira_token),
            headers={"Accept": "application/json"},
        )
        if r.status_code != 200:
            body: Any = r.text
            try:
                body = r.json()
            except Exception:
                pass
            raise JiraHttpError(r.status_code, f"Comment API {r.status_code}", body)
        data = r.json()
        comments = [normalize_comment_api_dict(c) for c in data.get("comments") or []]
        return {
            "comments": comments,
            "total": data.get("total", len(comments)),
            "start_at": data.get("startAt", start_at),
            "max_results": data.get("maxResults", max_results),
        }

    def get_issue_details(
        self,
        issue_key: str,
        *,
        include_comments: bool = True,
        max_comments: int = 50,
        include_changelog: bool = False,
        extra_fields: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Fetch issue details with ADF/rendered description, comments, attachments, custom fields, parent, links."""
        expand_parts = ["renderedFields"]
        if include_changelog:
            expand_parts.append("changelog")
        issue = self.jira.issue(issue_key, expand=",".join(expand_parts))
        if self.should_skip_issue(issue.fields.status.name):
            return None

        raw = issue.raw or {}
        fields_raw = raw.get("fields") or {}
        rendered = raw.get("renderedFields") or {}

        desc_raw = fields_raw.get("description")
        description_adf: Optional[Dict[str, Any]] = desc_raw if isinstance(desc_raw, dict) else None
        rendered_desc_val = rendered.get("description")
        description_rendered = rendered_desc_val if isinstance(rendered_desc_val, str) else None

        description_plain = ""
        if description_adf:
            description_plain = adf_to_plain_text(description_adf)
        if not description_plain and description_rendered:
            description_plain = html_to_plain(description_rendered)
        if not description_plain and desc_raw is not None and not isinstance(desc_raw, dict):
            text = str(desc_raw).strip()
            description_plain = text if not text.startswith("<") else html_to_plain(text)

        child_issues: List[Dict[str, str]] = []
        if issue.fields.issuetype.name == "Epic":
            jql = f'parent = {issue_key} OR "Epic Link" = {issue_key} ORDER BY created ASC'
            children = self.jira.search_issues(jql, maxResults=100)
            child_issues = [
                {"key": str(child.key), "summary": str(child.fields.summary)}
                for child in children
                if not self.should_skip_issue(child.fields.status.name)
            ]

        components: List[str] = []
        if hasattr(issue.fields, "components") and issue.fields.components:
            components = [str(c.name) for c in issue.fields.components]
        labels = list(issue.fields.labels) if hasattr(issue.fields, "labels") and issue.fields.labels else []

        comments_payload: Any = None
        if include_comments:
            try:
                comments_payload = self.get_issue_comments(
                    issue_key, start_at=0, max_results=max_comments
                )
            except JiraHttpError as e:
                comments_payload = {
                    "comments": [],
                    "total": 0,
                    "start_at": 0,
                    "max_results": max_comments,
                    "error": format_jira_exception(e),
                }

        changelog_out: Any = None
        if include_changelog:
            changelog_out = raw.get("changelog")

        result: Dict[str, Any] = {
            "key": str(issue.key),
            "summary": str(issue.fields.summary),
            "description": description_plain,
            "description_plain": description_plain,
            "description_rendered": description_rendered,
            "description_adf": description_adf,
            "status": str(issue.fields.status.name),
            "type": str(issue.fields.issuetype.name),
            "priority": str(issue.fields.priority.name)
            if hasattr(issue.fields, "priority") and issue.fields.priority
            else "None",
            "assignee": str(issue.fields.assignee.displayName) if issue.fields.assignee else "Unassigned",
            "reporter": str(issue.fields.reporter.displayName),
            "created": str(issue.fields.created),
            "updated": str(issue.fields.updated),
            "parent": self._parent_payload(fields_raw),
            "subtasks": self._subtasks_payload(issue, fields_raw),
            "child_issues": child_issues,
            "components": components,
            "labels": labels,
            "issuelinks": self._issuelinks_payload(issue, fields_raw),
            "attachments": self._attachments_payload(fields_raw),
            "custom_fields": self._build_custom_fields_map(fields_raw, extra_fields),
            "comments": comments_payload,
            "changelog": changelog_out,
        }
        return result

    def get_issue_for_migration(self, issue_key: str, include_closed: bool = False) -> Optional[Dict[str, Any]]:
        """
        Fetch full issue details suitable for migration/copy: includes components, labels,
        and description in a form usable for create_issues (ADF dict or structured).
        When include_closed is False, returns None for Done/Canceled issues.
        """
        issue = self.jira.issue(issue_key, expand='changelog,renderedFields')
        if not include_closed and self.should_skip_issue(issue.fields.status.name):
            return None
        components = [str(c.name) for c in (issue.fields.components or [])]
        labels = list(issue.fields.labels or [])
        desc = issue.fields.description
        if desc is None:
            description = None
        elif isinstance(desc, dict):
            description = desc  # ADF
        else:
            text = str(desc).strip()
            if text.startswith('<'):
                text = ''  # strip HTML
            description = {
                'version': 1,
                'type': 'doc',
                'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': text or '(No description)'}]}]
            } if text else None
        assignee_name = None
        if issue.fields.assignee:
            assignee_name = getattr(issue.fields.assignee, 'name', None) or getattr(issue.fields.assignee, 'accountId', None) or str(issue.fields.assignee.displayName)
        epic_link = None
        for attr in ('customfield_10014', 'parent'):
            val = getattr(issue.fields, attr, None)
            if val is not None:
                epic_link = getattr(val, 'key', None) or (val if isinstance(val, str) else None)
                if epic_link:
                    break
        parent_key = None
        if getattr(issue.fields, 'parent', None):
            parent_key = getattr(issue.fields.parent, 'key', None) or (issue.fields.parent if isinstance(issue.fields.parent, str) else None)
        fix_versions = []
        for v in (getattr(issue.fields, "fixVersions", None) or []):
            name = getattr(v, "name", None) or (v.get("name") if isinstance(v, dict) else None)
            vid = getattr(v, "id", None) or (v.get("id") if isinstance(v, dict) else None)
            if name or vid:
                fix_versions.append({"id": str(vid) if vid else None, "name": str(name) if name else ""})
        affects_versions = []
        aff = getattr(issue.fields, "versions", None) or getattr(issue.fields, "affectsVersions", None)
        for v in (aff or []):
            name = getattr(v, "name", None) or (v.get("name") if isinstance(v, dict) else None)
            vid = getattr(v, "id", None) or (v.get("id") if isinstance(v, dict) else None)
            if name or vid:
                affects_versions.append({"id": str(vid) if vid else None, "name": str(name) if name else ""})
        return {
            'key': str(issue.key),
            'summary': str(issue.fields.summary),
            'type': str(issue.fields.issuetype.name),
            'priority': str(issue.fields.priority.name) if hasattr(issue.fields, 'priority') and issue.fields.priority else 'None',
            'assignee': assignee_name,
            'labels': labels,
            'components': components,
            'description': description,
            'status': str(issue.fields.status.name),
            'epic_link': epic_link,
            'parent': parent_key,
            'fix_versions': fix_versions,
            'affects_versions': affects_versions,
            'subtasks': [{'key': str(s.key), 'summary': str(s.fields.summary)} for s in (issue.fields.subtasks or [])],
            'issuelinks': [
                {
                    'type': str(link.type.name),
                    'inward': str(link.inwardIssue.key) if getattr(link, 'inwardIssue', None) else None,
                    'outward': str(link.outwardIssue.key) if getattr(link, 'outwardIssue', None) else None,
                }
                for link in (issue.fields.issuelinks or [])
            ],
        }

    def get_related_issues(self, issue_key: str, **detail_kwargs: Any) -> List[Dict]:
        """Recursively fetch all related issues."""
        if issue_key in self.processed_issues:
            return []
        self.processed_issues.add(issue_key)
        issue_details = self.get_issue_details(issue_key, **detail_kwargs)
        if issue_details is None:
            return []
        related_issues = []
        for link in issue_details['issuelinks']:
            for direction in ['inward', 'outward']:
                related_key = link[direction]
                if related_key and related_key not in self.processed_issues:
                    related_issues.extend(self.get_related_issues(related_key, **detail_kwargs))
        for subtask in issue_details['subtasks']:
            if subtask['key'] not in self.processed_issues:
                related_issues.extend(self.get_related_issues(subtask['key'], **detail_kwargs))
        if 'child_issues' in issue_details:
            for child in issue_details['child_issues']:
                if child['key'] not in self.processed_issues:
                    related_issues.extend(self.get_related_issues(child['key'], **detail_kwargs))
        return [issue_details] + related_issues

    def generate_markdown(self, issues: List[Dict]) -> str:
        md = ["# Jira Issue Analysis Report\n", f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"]
        for issue in issues:
            md.extend([f"## {issue['key']}: {issue['summary']}\n", f"**Type:** {issue['type']}  \n",
                       f"**Status:** {issue['status']}  \n", f"**Priority:** {issue['priority']}  \n",
                       f"**Assignee:** {issue['assignee']}  \n", f"**Reporter:** {issue['reporter']}  \n",
                       f"**Created:** {issue['created']}  \n", f"**Updated:** {issue['updated']}  \n\n"])
            parent = issue.get("parent")
            if parent and parent.get("key"):
                line = f"**Parent:** {parent['key']}"
                if parent.get("summary"):
                    line += f" — {parent['summary']}"
                md.extend([line + "  \n\n"])
            desc_text = (issue.get("description_plain") or issue.get("description") or "").strip()
            if desc_text:
                md.extend(["### Description\n", f"{desc_text}\n\n"])
            elif issue.get("description_rendered"):
                md.extend(["### Description\n", f"{html_to_plain(issue['description_rendered'])}\n\n"])
            cdata = issue.get("comments")
            if isinstance(cdata, dict) and cdata.get("comments"):
                md.append("### Comments\n")
                for c in cdata["comments"]:
                    auth = (c.get("author") or {}).get("displayName") or "Unknown"
                    md.append(f"- **{auth}** ({c.get('created', '')}): {c.get('body', '')}\n")
                md.append("\n")
            if issue.get("attachments"):
                md.append("### Attachments\n")
                for att in issue["attachments"]:
                    fn = att.get("filename") or "?"
                    sz = att.get("size")
                    mt = att.get("mimeType") or ""
                    md.append(f"- {fn} ({sz} bytes, {mt})\n")
                md.append("\n")
            cf = issue.get("custom_fields") or {}
            if cf:
                md.append("### Custom fields\n")
                for fid in sorted(cf.keys()):
                    meta = cf[fid]
                    md.append(f"- **{meta.get('name', fid)}**: {meta.get('value')}\n")
                md.append("\n")
            if issue.get("subtasks"):
                md.append("### Subtasks\n")
                for subtask in issue["subtasks"]:
                    md.append(f"- {subtask['key']}: {subtask['summary']}\n")
                md.append("\n")
            if issue.get("child_issues"):
                md.append("### Child Issues\n")
                for child in issue["child_issues"]:
                    md.append(f"- {child['key']}: {child['summary']}\n")
                md.append("\n")
            if issue.get("issuelinks"):
                md.append("### Linked Issues\n")
                for link in issue["issuelinks"]:
                    if link.get("inward"):
                        md.append(f"- {link['type']} ← {link['inward']}\n")
                    if link.get("outward"):
                        md.append(f"- {link['type']} → {link['outward']}\n")
                md.append("\n")
            md.append("---\n\n")
        return "".join(md)

    def create_issues(self, issues: List[Dict[str, Any]], dry_run: bool = False) -> List[Dict[str, Any]]:
        results = []
        createmeta_cache: Dict[str, Dict[str, Any]] = {}
        for issue_data in issues:
            try:
                fields = self._build_create_fields(issue_data)
                if dry_run:
                    project_key = issue_data.get("project") or ""
                    if project_key not in createmeta_cache:
                        try:
                            createmeta_cache[project_key] = self.get_createmeta(project_keys=project_key)
                        except Exception:
                            createmeta_cache[project_key] = {}
                    validation = self._validate_create_payload(
                        issue_data, createmeta=createmeta_cache.get(project_key) or None
                    )
                    results.append({
                        "issue_key": None,
                        "status": "dry_run",
                        "message": "Would create",
                        "payload_summary": {
                            "project": issue_data["project"],
                            "summary": issue_data["summary"],
                            "type": issue_data.get("type", "Task"),
                            **({"sprint": issue_data["sprint"]} if issue_data.get("sprint") is not None else {}),
                        },
                        "validation": {
                            "accuracy": validation["accuracy"],
                            "accuracy_rating": validation["accuracy_rating"],
                            "valid": validation["valid"],
                            "missing_required": validation.get("missing_required", []),
                            "errors": validation.get("errors", []),
                        },
                    })
                else:
                    new_issue = self.jira.create_issue(fields=fields)
                    message = f"Successfully created issue {new_issue.key}"
                    if issue_data.get("sprint") is not None:
                        try:
                            move_res = self._move_issue_to_sprint(
                                new_issue.key, issue_data["sprint"], project_key=issue_data.get("project")
                            )
                            message += f" (sprint: {move_res.get('sprint')})"
                        except Exception as e:
                            message += f" (sprint assignment failed: {e})"
                    results.append({"issue_key": new_issue.key, "status": "success", "message": message})
            except Exception as e:
                results.append({"issue_key": issue_data.get("project", "UNKNOWN"), "status": "error", "message": str(e)})
        return results

    def update_issues(self, updates: Dict[str, Dict[str, Any]], dry_run: bool = False) -> List[Dict[str, Any]]:
        results = []
        for issue_key, fields_to_update in updates.items():
            try:
                issue = self.jira.issue(issue_key)
                update_payload = {}
                if 'summary' in fields_to_update:
                    update_payload['summary'] = fields_to_update['summary']
                if 'description' in fields_to_update:
                    update_payload['description'] = self.coerce_description_to_adf(fields_to_update['description'])
                if 'change_notes' in fields_to_update and 'status' not in fields_to_update:
                    update_payload[self.CHANGE_NOTES_FIELD] = self.build_adf_comment(fields_to_update['change_notes'])
                if 'delivered_in_build' in fields_to_update and 'status' not in fields_to_update:
                    update_payload[self.DELIVERED_IN_BUILD_FIELD] = fields_to_update['delivered_in_build']
                version_payload, version_audit = self._build_version_update_fields(
                    issue, issue_key, fields_to_update
                )
                update_payload.update(version_payload)
                transitions_summary = []
                if 'status' in fields_to_update:
                    transitions_summary = self._get_transition_path_summary(issue, fields_to_update['status'])
                if dry_run:
                    payload_summary = {}
                    if update_payload:
                        payload_summary["fields"] = list(update_payload.keys())
                    if transitions_summary:
                        payload_summary["transitions"] = transitions_summary
                    if "comment" in fields_to_update:
                        payload_summary["comment"] = True
                    if "assignee" in fields_to_update:
                        payload_summary["assignee"] = fields_to_update["assignee"]
                    if "sprint" in fields_to_update:
                        payload_summary["sprint"] = fields_to_update["sprint"]
                    if "worklog" in fields_to_update:
                        payload_summary["worklog"] = fields_to_update["worklog"]
                    if version_audit:
                        payload_summary["versions"] = version_audit
                    update_field_ids = list(update_payload.keys()) if update_payload else []
                    validation = self._validate_update_payload(update_field_ids)
                    results.append({
                        "issue_key": issue_key,
                        "status": "dry_run",
                        "message": "Would update",
                        "payload_summary": payload_summary,
                        "validation": {
                            "accuracy": validation["accuracy"],
                            "accuracy_rating": validation["accuracy_rating"],
                            "valid": validation["valid"],
                            "unknown_fields": validation.get("unknown_fields", []),
                            "errors": validation.get("errors", []),
                        },
                    })
                else:
                    if update_payload:
                        issue.update(fields=update_payload)
                        print(f"  - Updated fields for {issue_key}: {', '.join(update_payload.keys())}.")
                    if 'assignee' in fields_to_update:
                        self._set_assignee(issue_key, fields_to_update['assignee'])
                        print(f"  - Updated assignee for {issue_key}.")
                    if 'sprint' in fields_to_update:
                        move_res = self._move_issue_to_sprint(
                            issue_key,
                            fields_to_update['sprint'],
                            project_key=self._issue_project_key(issue, issue_key),
                        )
                        target = move_res.get('sprint')
                        print(
                            f"  - Moved {issue_key} to {move_res['moved_to']}"
                            + (f" {target}" if target is not None else "") + "."
                        )
                    if 'status' in fields_to_update:
                        worklog = fields_to_update.get('worklog')
                        transition_result = self.transition_to_status(
                            issue_key,
                            fields_to_update['status'],
                            change_notes=fields_to_update.get('change_notes'),
                            delivered_in_build=fields_to_update.get('delivered_in_build'),
                            worklog=worklog,
                        )
                        if transition_result.get("status") != "success":
                            results.append({
                                'issue_key': issue_key,
                                'status': transition_result.get('status', 'error'),
                                'message': transition_result.get('message', 'Transition incomplete'),
                                'transition': transition_result,
                            })
                            continue
                    elif 'worklog' in fields_to_update:
                        wl = self._normalize_worklog_input(fields_to_update['worklog']) or {"time_spent": "15m"}
                        self.log_work(
                            issue_key,
                            wl["time_spent"],
                            comment=wl.get("comment"),
                            started=wl.get("started"),
                        )
                    if 'comment' in fields_to_update:
                        print(f"  - Adding final comment to {issue_key}.")
                        self.jira.add_comment(issue_key, body=self.build_adf_comment(fields_to_update['comment']))
                    results.append({'issue_key': issue_key, 'status': 'success', 'message': 'Successfully updated'})
            except Exception as e:
                results.append({'issue_key': issue_key, 'status': 'error', 'message': str(e)})
        return results

    def _get_transition_path_summary(self, issue: Any, target_status: str) -> List[str]:
        """Return transition steps (name -> to_status) to reach target_status."""
        target_status = self._normalize_status_name(target_status)
        current_status = issue.fields.status.name
        if self._normalize_status_name(current_status).lower() == target_status.lower():
            return [current_status]
        try:
            transitions = self.get_issue_transitions(issue.key)
        except Exception:
            return [current_status, f"-> {target_status}"]
        steps: List[str] = [current_status]
        simulated_current = current_status
        for _ in range(10):
            if self._normalize_status_name(simulated_current).lower() == target_status.lower():
                break
            next_transition = self._pick_next_transition_on_path(
                transitions, simulated_current, target_status
            )
            if not next_transition:
                steps.append(f"-> {target_status}")
                break
            to_status = self._transition_target_name(next_transition) or "?"
            steps.append(f"{next_transition.get('name')} (-> {to_status})")
            simulated_current = to_status
        return steps

    def _strip_version_prefix(self, name: str, prefix_strip: str, target_project: str) -> str:
        """
        Strip redundant prefix from version name for target project.
        e.g. 'PREFIX - 7.0.0' with prefix 'PREFIX - ' -> '7.0.0'
        Supports '{target_project} - ' to use target project name as prefix.
        """
        if not name or not prefix_strip:
            return name
        prefix = prefix_strip.replace("{target_project}", target_project)
        if name.startswith(prefix):
            return name[len(prefix) :].strip()
        return name

    def _prepare_versions_for_migration(
        self,
        pre_move_links: Dict[str, Dict[str, Any]],
        source_project: str,
        target_project: str,
        version_prefix_strip: Optional[str] = None,
        dry_run: bool = False,
        include_released_versions: bool = True,
    ) -> Dict[str, Any]:
        """
        Collect fix/affects versions from issues, normalize names, and ensure versions exist in
        target project. In dry run, do not create; return plan for audit.
        """
        all_source_names: Set[str] = set()
        for info in pre_move_links.values():
            for v in info.get("fix_versions") or []:
                n = (v.get("name") or "").strip()
                if n:
                    all_source_names.add(n)
            for v in info.get("affects_versions") or []:
                n = (v.get("name") or "").strip()
                if n:
                    all_source_names.add(n)
        existing = {
            (v.get("name", "").strip()): str(v.get("id", ""))
            for v in self.get_project_versions(target_project)
            if v.get("name")
        }
        source_released_by_name: Dict[str, bool] = {}
        try:
            for v in self.get_project_versions(source_project):
                n = (v.get("name") or "").strip()
                if n:
                    source_released_by_name[n] = bool(v.get("released"))
        except Exception:
            source_released_by_name = {}
        name_to_target_id: Dict[str, str] = {}
        source_to_target_name: Dict[str, str] = {}
        existing_matches: List[Dict[str, str]] = []
        created_versions: List[Dict[str, str]] = []
        would_create_versions: List[Dict[str, str]] = []
        failed_versions: List[Dict[str, str]] = []
        unmapped_source_names: List[str] = []
        skipped_released_versions: List[str] = []
        for src_name in sorted(all_source_names):
            if not include_released_versions and source_released_by_name.get(src_name) is True:
                skipped_released_versions.append(src_name)
                continue
            target_name = (
                self._strip_version_prefix(src_name, version_prefix_strip, target_project)
                if version_prefix_strip
                else src_name
            )
            if not target_name:
                unmapped_source_names.append(src_name)
                continue
            source_to_target_name[src_name] = target_name
            if target_name in existing:
                name_to_target_id[src_name] = existing[target_name]
                existing_matches.append(
                    {
                        "source_name": src_name,
                        "target_name": target_name,
                        "target_id": existing[target_name],
                    }
                )
            else:
                if dry_run:
                    would_create_versions.append(
                        {"source_name": src_name, "target_name": target_name}
                    )
                    continue
                created = self.create_version(target_project, target_name)
                if created and created.get("id"):
                    vid = str(created["id"])
                    existing[target_name] = vid
                    name_to_target_id[src_name] = vid
                    created_versions.append(
                        {"source_name": src_name, "target_name": target_name, "target_id": vid}
                    )
                    print(f"  Created version '{target_name}' in {target_project} (from '{src_name}')")
                else:
                    failed_versions.append(
                        {"source_name": src_name, "target_name": target_name}
                    )
        return {
            "name_to_target_id": name_to_target_id,
            "source_to_target_name": source_to_target_name,
            "existing_matches": existing_matches,
            "created_versions": created_versions,
            "would_create_versions": would_create_versions,
            "failed_versions": failed_versions,
            "unmapped_source_names": unmapped_source_names,
            "skipped_released_versions": skipped_released_versions,
        }

    def _apply_versions_after_move(
        self,
        mapping: Dict[str, str],
        pre_move_links: Dict[str, Dict[str, Any]],
        version_name_to_id: Dict[str, str],
    ) -> None:
        """
        After bulk move, set fixVersions and affects versions on each new issue using the
        mapped target version IDs.
        """
        if not version_name_to_id:
            return
        for old_key, new_key in mapping.items():
            info = pre_move_links.get(old_key)
            if not info:
                continue
            fix_ids = []
            for v in info.get("fix_versions") or []:
                src_name = (v.get("name") or "").strip()
                tid = version_name_to_id.get(src_name) if src_name else None
                if tid:
                    fix_ids.append(tid)
            aff_ids = []
            for v in info.get("affects_versions") or []:
                src_name = (v.get("name") or "").strip()
                tid = version_name_to_id.get(src_name) if src_name else None
                if tid:
                    aff_ids.append(tid)
            if not fix_ids and not aff_ids:
                continue
            update_fields: Dict[str, Any] = {}
            if fix_ids:
                update_fields["fixVersions"] = [{"id": i} for i in fix_ids]
            if aff_ids:
                update_fields["versions"] = [{"id": i} for i in aff_ids]
            if update_fields:
                try:
                    self.jira.issue(new_key).update(fields=update_fields)
                    print(f"  Set versions on {new_key} (fix: {fix_ids}, affects: {aff_ids})")
                except Exception as e:
                    print(f"  - Warning: could not set versions on {new_key}: {e}")

    def _bulk_move_issues(
        self,
        issue_keys: List[str],
        new_project_key: str,
        infer_field_defaults: bool = True,
        infer_status_defaults: bool = True,
        infer_classification_defaults: bool = True,
        target_mandatory_fields: Optional[Dict[str, Any]] = None,
        key_to_target_parent: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """
        Call Jira POST /rest/api/3/bulk/issues/move (v3 API). Returns mapping old_key -> new_key.
        Builds targetToSourcesMapping by grouping issues by target issue type (from target project createmeta).
        If the API returns a task ID (async), poll bulk-queue until complete and extract mapping.
        """
        if not issue_keys:
            return {}
        url = f"{self.jira_url}/rest/api/3/bulk/issues/move"
        # Target project issue type name -> id (e.g. Task -> 10001)
        createmeta = self.get_createmeta(project_keys=new_project_key)
        type_name_to_id: Dict[str, str] = {}
        for proj in createmeta.get("projects") or []:
            if (proj.get("key") or "").upper() != new_project_key.upper():
                continue
            for it in proj.get("issuetypes") or []:
                name = (it.get("name") or "").strip()
                tid = it.get("id")
                if name and tid is not None:
                    type_name_to_id[name] = str(tid)
            break
        if not type_name_to_id:
            raise RuntimeError(f"No issue types found for target project {new_project_key} (createmeta).")
        # Resolve placeholder mandatory fields: "versions": [] -> first project version id if target requires it
        resolved_mandatory = None
        if target_mandatory_fields:
            resolved_mandatory = dict(target_mandatory_fields)
            if "versions" in resolved_mandatory and resolved_mandatory["versions"] == []:
                try:
                    rv = requests.get(
                        f"{self.jira_url}/rest/api/3/project/{new_project_key}/versions",
                        auth=(self.jira_email, self.jira_token),
                        headers={"Accept": "application/json"},
                    )
                    if rv.status_code == 200:
                        vers = rv.json()
                        if vers and isinstance(vers, list):
                            non_archived = [v for v in vers if isinstance(v, dict) and not v.get("archived")]
                            use_list = non_archived if non_archived else vers
                            # When target has only one version (or one non-archived), use it
                            if len(use_list) == 1:
                                first_id = use_list[0].get("id") if isinstance(use_list[0], dict) else getattr(use_list[0], "id", None)
                                if first_id is not None:
                                    resolved_mandatory["versions"] = [str(first_id)]
                                    print(f"  Using single target version: {first_id}")
                            else:
                                # Prefer first non-archived
                                for v in use_list:
                                    first_id = v.get("id") if isinstance(v, dict) else getattr(v, "id", None)
                                    if first_id is not None:
                                        resolved_mandatory["versions"] = [str(first_id)]
                                        break
                                if resolved_mandatory["versions"] == [] and vers:
                                    first_id = vers[0].get("id") if isinstance(vers[0], dict) else getattr(vers[0], "id", None)
                                    if first_id is not None:
                                        resolved_mandatory["versions"] = [str(first_id)]
                except Exception:
                    pass
                if resolved_mandatory.get("versions") == []:
                    raise RuntimeError(
                        f"Target project {new_project_key} has no (non-archived) versions and bulk_move_target_mandatory_fields has 'versions': []. "
                        "Add a version in Jira (Project settings → Versions) or set 'versions' to a version id, e.g. \"versions\": [\"12345\"]."
                    )
            # Normalize version ids to list of strings (allow config to pass e.g. [12345] or ["12345"])
            if "versions" in resolved_mandatory and resolved_mandatory["versions"]:
                resolved_mandatory["versions"] = [str(v) for v in resolved_mandatory["versions"]]
            target_mandatory_fields = resolved_mandatory
        # Resolve each key to issue type name (one search for all)
        jql = " OR ".join(f'key = "{k}"' for k in issue_keys[:100])
        search_res = self.search_issues_direct(jql, max_results=len(issue_keys) + 10)
        issues_data = search_res.get("issues") or search_res.get("values") or []
        key_to_type: Dict[str, str] = {}
        for issue in issues_data:
            key = issue.get("key") if isinstance(issue, dict) else getattr(issue, "key", None)
            if not key:
                continue
            fields = issue.get("fields", {}) if isinstance(issue, dict) else {}
            it = fields.get("issuetype") or {}
            name = it.get("name") if isinstance(it, dict) else getattr(it, "name", None)
            if name:
                key_to_type[key] = str(name)
        # Fallback: any key not found gets "Task"
        for k in issue_keys:
            if k not in key_to_type:
                key_to_type[k] = "Task"
        # Group by target mapping key: "projectKey,issueTypeId" or "projectKey,issueTypeId,parentKey" for subtasks
        groups: Dict[str, List[str]] = {}
        for k in issue_keys:
            type_name = key_to_type.get(k, "Task")
            type_id = type_name_to_id.get(type_name) or type_name_to_id.get("Task") or next(iter(type_name_to_id.values()), None)
            if not type_id:
                raise RuntimeError(f"Target project {new_project_key} has no issue type matching '{type_name}' or 'Task'.")
            if key_to_target_parent and k in key_to_target_parent:
                map_key = f"{new_project_key},{type_id},{key_to_target_parent[k]}"
            else:
                map_key = f"{new_project_key},{type_id}"
            groups.setdefault(map_key, []).append(k)
        target_to_sources = {}
        for map_key, keys in groups.items():
            entry = {
                "issueIdsOrKeys": keys,
                "inferFieldDefaults": infer_field_defaults if not target_mandatory_fields else False,
                "inferStatusDefaults": infer_status_defaults,
                "inferClassificationDefaults": infer_classification_defaults,
                "inferSubtaskTypeDefault": True,
            }
            if target_mandatory_fields:
                # API: targetMandatoryFields not allowed when inferFieldDefaults is true, so we force false above.
                # Version fields (versions, fixVersions) must use {"value": [id, ...]} per API doc; other fields use raw.
                fields_payload = {}
                for fid, val in target_mandatory_fields.items():
                    if fid in ("versions", "fixVersions"):
                        fields_payload[fid] = {"value": val if isinstance(val, list) else [val]}
                    else:
                        fields_payload[fid] = {"retain": False, "type": "raw", "value": val}
                entry["targetMandatoryFields"] = [{"fields": fields_payload}]
            target_to_sources[map_key] = entry
        # API allows only one target (project, issue type, parent) per request; send one request per group
        mapping: Dict[str, str] = {}
        for map_key, entry in target_to_sources.items():
            keys_in_group = entry["issueIdsOrKeys"]
            print(f"  -> API request for {map_key}: {len(keys_in_group)} issues")
            payload = {
                "sendBulkNotification": True,
                "targetToSourcesMapping": {map_key: entry},
            }
            resp = requests.post(
                url,
                json=payload,
                auth=(self.jira_email, self.jira_token),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            if resp.status_code != 200 and resp.status_code != 201:
                err_body = resp.text
                err_json = None
                try:
                    err_json = resp.json()
                    err_body = json.dumps(err_json, indent=2)
                except Exception:
                    pass
                hint = ""
                if resp.status_code == 400 and err_json and target_mandatory_fields:
                    errors = err_json.get("errors") or []
                    if errors and isinstance(errors[0], dict):
                        msg = (errors[0].get("message") or "")
                        if "Affects versions" in msg or "versions" in msg.lower():
                            hint = (
                                "\n\nHint: The bulk move API auto-includes a parent's subtasks in the same request, "
                                "but targetMandatoryFields apply only to the parent issue type. So subtasks fail "
                                "when the target project requires e.g. 'Affects versions'. Fix: align the target "
                                "project's field configuration with the source (same field configuration scheme as IS) "
                                "so 'Affects versions' is not required."
                            )
                raise RuntimeError(f"Bulk move API {resp.status_code}: {err_body}{hint}")
            data = resp.json()
            if "taskId" in data:
                task_id = data["taskId"]
                status_url = f"{self.jira_url}/rest/api/3/bulk/queue/{task_id}"
                for _ in range(60):
                    r = requests.get(status_url, auth=(self.jira_email, self.jira_token), headers={"Accept": "application/json"})
                    r.raise_for_status()
                    task_data = r.json()
                    status = (task_data.get("status") or "").upper()
                    if status == "COMPLETE":
                        result = task_data.get("result", {})
                        moved = result.get("movedIssues", []) or result.get("issues", []) or []
                        if moved:
                            for item in moved:
                                old_key = item.get("oldKey") or item.get("key")
                                new_key = item.get("newKey") or item.get("key")
                                if old_key and new_key:
                                    mapping[old_key] = new_key
                        else:
                            new_ids = task_data.get("processedAccessibleIssues") or []
                            for i, old_key in enumerate(keys_in_group):
                                if i < len(new_ids):
                                    new_id = new_ids[i]
                                    try:
                                        ir = requests.get(
                                            f"{self.jira_url}/rest/api/3/issue/{new_id}",
                                            auth=(self.jira_email, self.jira_token),
                                            headers={"Accept": "application/json"},
                                            params={"fields": "key"},
                                        )
                                        if ir.status_code == 200:
                                            mapping[old_key] = ir.json().get("key", "")
                                    except Exception:
                                        pass
                        break
                    if status in ("FAILED", "REJECTED", "CANCELLED"):
                        raise RuntimeError(f"Bulk move task failed: {task_data}")
                    time.sleep(1)
                else:
                    raise RuntimeError("Bulk move task timed out")
            else:
                for item in data.get("movedIssues", []) or data.get("issues", []) or []:
                    old_key = item.get("oldKey") or item.get("key")
                    new_key = item.get("newKey") or item.get("key")
                    if old_key and new_key:
                        mapping[old_key] = new_key
        return mapping

    def _resolve_issue_keys_with_children(self, issue_keys: List[str], include_closed: bool) -> List[str]:
        """Expand issue_keys to include all subtasks and epic children. Deduplicate."""
        expanded = set()
        for key in issue_keys:
            expanded.add(key)
            details = self.get_issue_for_migration(key, include_closed=include_closed)
            if not details:
                continue
            for s in details.get("subtasks", []):
                expanded.add(s["key"])
            issue = self.jira.issue(key)
            if getattr(issue.fields, "issuetype", None) and str(issue.fields.issuetype.name) == "Epic":
                jql = f'"Epic Link" = {key} OR parent = {key}'
                try:
                    children = self.jira.search_issues(jql, maxResults=100)
                    for c in children:
                        expanded.add(c.key)
                except Exception:
                    pass
        return list(expanded)

    def _add_comment(self, issue_key: str, body: str) -> None:
        """Add a comment to an issue (ADF)."""
        self.jira.add_comment(issue_key, body=self.build_adf_comment(body))

    def _create_issue_link(self, inward_key: str, outward_key: str, link_type: str = "Relates to") -> None:
        """Create an issue link between two issues. Link type name must exist in Jira."""
        try:
            self.jira.create_issue_link(type=link_type, inwardIssue=inward_key, outwardIssue=outward_key)
        except Exception as e:
            print(f"  - Warning: could not create link {inward_key} <-> {outward_key}: {e}")

    def _set_epic_link(self, issue_key: str, epic_key: str, epic_link_field_id: str = "customfield_10014") -> None:
        """Set the Epic Link (or parent) field on an issue. Uses customfield_10014 by default (Jira Cloud)."""
        try:
            self.jira.issue(issue_key).update(fields={epic_link_field_id: epic_key})
        except Exception as e:
            print(f"  - Warning: could not set epic link on {issue_key} to {epic_key}: {e}")

    def _copy_epic_to_target(self, epic_key: str, target_project: str, component_map: Dict[str, str]) -> Optional[str]:
        """Copy a single epic to the target project. Returns new issue key or None."""
        full = self.get_issue_for_migration(epic_key, include_closed=True)
        if not full or full.get("type") != "Epic":
            return None
        comps = full.get("components") or []
        create_payload = {
            "project": target_project,
            "summary": full["summary"],
            "type": "Epic",
            "description": full.get("description"),
            "priority": full.get("priority"),
            "labels": full.get("labels"),
            "components": [component_map.get(c, c) for c in comps],
        }
        if full.get("assignee"):
            create_payload["assignee"] = full["assignee"]
        created = self.create_issues([create_payload], dry_run=False)
        if created and created[0].get("status") == "success":
            return created[0]["issue_key"]
        return None

    def migrate_issues(
        self,
        source_project: str,
        target_project: str,
        jql: str,
        options: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
        mapping_file_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Migrate issues from source to target project. mode: 'move' (default) or 'copy'.
        Options: mode, migrate_closed, component_filter, summary_include, infer_field_defaults,
        infer_status_defaults, infer_classification_defaults, comment_previous_key, comment_on_source,
        component_map (copy only).
        Returns dict with 'mapping' (old_key -> new_key) and optionally 'mapping_file'.
        """
        opts = options or {}
        mode = (opts.get("mode") or "move").lower()
        migrate_closed = opts.get("migrate_closed", False)
        component_filter = opts.get("component_filter") or []
        summary_include = opts.get("summary_include") or ""
        infer_field_defaults = opts.get("infer_field_defaults", True)
        infer_status_defaults = opts.get("infer_status_defaults", True)
        infer_classification_defaults = opts.get("infer_classification_defaults", True)
        comment_previous_key = opts.get("comment_previous_key", False)
        comment_on_source = opts.get("comment_on_source", False)
        component_map = opts.get("component_map") or {}
        bulk_move_target_mandatory_fields = opts.get("bulk_move_target_mandatory_fields")
        version_prefix_strip = opts.get("version_prefix_strip")
        migrate_closed_for_unreleased_versions = opts.get("migrate_closed_for_unreleased_versions", True)
        migrate_released_versions = opts.get("migrate_released_versions", True)
        exclude_closed_if_all_fixversions_released_and_missing_target = opts.get(
            "exclude_closed_if_all_fixversions_released_and_missing_target",
            False,
        )

        search_result = self.search_issues_direct_all(jql, page_size=100)
        # Support both {"issues": [...]} and paginated {"values": [...]} shapes
        issues_data = (
            search_result.get("issues")
            or search_result.get("values")
            or (search_result if isinstance(search_result, list) else [])
        )
        if not isinstance(issues_data, list):
            issues_data = []
        candidate_keys = []
        skipped_closed = 0
        skipped_other = 0
        selected_audit: List[Dict[str, Any]] = []
        skipped_audit: List[Dict[str, Any]] = []
        source_unreleased_by_name: Set[str] = set()
        source_released_by_name: Dict[str, bool] = {}
        target_existing_version_names: Set[str] = set()
        if (not migrate_closed) or exclude_closed_if_all_fixversions_released_and_missing_target:
            try:
                source_versions = self.get_project_versions(source_project)
                source_unreleased_by_name = {
                    (v.get("name") or "").strip()
                    for v in source_versions
                    if v.get("name") and not v.get("released")
                }
                source_released_by_name = {
                    (v.get("name") or "").strip(): bool(v.get("released"))
                    for v in source_versions
                    if v.get("name")
                }
            except Exception:
                source_unreleased_by_name = set()
                source_released_by_name = {}
        if exclude_closed_if_all_fixversions_released_and_missing_target:
            try:
                target_existing_version_names = {
                    (v.get("name") or "").strip()
                    for v in self.get_project_versions(target_project)
                    if v.get("name")
                }
            except Exception:
                target_existing_version_names = set()
        for issue in issues_data:
            key = issue.get("key", None) if isinstance(issue, dict) else getattr(issue, "key", None)
            if not key:
                skipped_other += 1
                skipped_audit.append({"key": "(missing)", "reason": "missing_key"})
                continue
            fields = issue.get("fields", {}) if isinstance(issue, dict) else getattr(issue, "fields", None)
            if fields is not None:
                if isinstance(fields, dict):
                    status_obj = fields.get("status") or {}
                    status_name = (status_obj.get("name") or "")
                    fix_versions = fields.get("fixVersions") or []
                    is_closed = self._is_done_status(status_obj)
                    include_reason = "open_status"
                    if is_closed:
                        if exclude_closed_if_all_fixversions_released_and_missing_target and fix_versions:
                            source_fix_names = [
                                (v.get("name") or "").strip()
                                for v in fix_versions
                                if isinstance(v, dict) and (v.get("name") or "").strip()
                            ]
                            if source_fix_names:
                                all_released = all(
                                    source_released_by_name.get(n) is True for n in source_fix_names
                                )
                                normalized_target_names = [
                                    self._strip_version_prefix(n, version_prefix_strip, target_project)
                                    if version_prefix_strip
                                    else n
                                    for n in source_fix_names
                                ]
                                all_missing_in_target = all(
                                    n not in target_existing_version_names for n in normalized_target_names if n
                                )
                                if all_released and all_missing_in_target:
                                    skipped_closed += 1
                                    skipped_audit.append(
                                        {
                                            "key": key,
                                            "status": status_name,
                                            "reason": "closed_all_fix_versions_released_and_missing_target",
                                            "fix_versions": source_fix_names,
                                            "normalized_target_versions": normalized_target_names,
                                        }
                                    )
                                    continue
                        if migrate_closed:
                            include_reason = "closed_included_by_migrate_closed"
                        elif migrate_closed_for_unreleased_versions:
                            has_unreleased_fix = any(
                                self._version_is_unreleased(v, source_unreleased_by_name)
                                for v in fix_versions
                                if isinstance(v, dict)
                            )
                            if not has_unreleased_fix:
                                skipped_closed += 1
                                skipped_audit.append(
                                    {
                                        "key": key,
                                        "status": status_name,
                                        "reason": "closed_without_unreleased_fix_version",
                                        "fix_versions": [v.get("name") for v in fix_versions if isinstance(v, dict)],
                                    }
                                )
                                continue
                            include_reason = "closed_with_unreleased_fix_version"
                        else:
                            skipped_closed += 1
                            skipped_audit.append(
                                {
                                    "key": key,
                                    "status": status_name,
                                    "reason": "closed_excluded_default",
                                    "fix_versions": [v.get("name") for v in fix_versions if isinstance(v, dict)],
                                }
                            )
                            continue
                    if component_filter:
                        comps = [c.get("name") for c in (fields.get("components") or []) if isinstance(c, dict)]
                        if not any(c in component_filter for c in comps):
                            skipped_other += 1
                            skipped_audit.append({"key": key, "status": status_name, "reason": "component_filter"})
                            continue
                    summary = (fields.get("summary") or "") or ""
                    if summary_include and summary_include not in summary:
                        skipped_other += 1
                        skipped_audit.append({"key": key, "status": status_name, "reason": "summary_filter"})
                        continue
                    selected_audit.append(
                        {
                            "key": key,
                            "status": status_name,
                            "reason": include_reason,
                            "fix_versions": [v.get("name") for v in fix_versions if isinstance(v, dict)],
                        }
                    )
            candidate_keys.append(key)

        if not candidate_keys:
            msg = f"No issues matched filters ({len(issues_data)} from JQL"
            if skipped_closed or skipped_other:
                msg += f", {skipped_closed} excluded by closed status, {skipped_other} by other filters"
            msg += "). Set options.migrate_closed=true to include all closed, or options.migrate_closed_for_unreleased_versions=true to include closed issues tied to unreleased fixVersions."
            return {"mapping": {}, "message": msg}
        selection_skip_reasons: Dict[str, int] = {}
        for item in skipped_audit:
            reason = item.get("reason", "unknown")
            selection_skip_reasons[reason] = selection_skip_reasons.get(reason, 0) + 1

        mapping = {}

        if mode == "move":
            include_closed_for_expansion = bool(migrate_closed or migrate_closed_for_unreleased_versions)
            all_keys = self._resolve_issue_keys_with_children(candidate_keys, include_closed_for_expansion)
            if dry_run:
                pre_move_links = {}
                for k in all_keys:
                    info = self.get_issue_for_migration(k, include_closed=True)
                    if info:
                        pre_move_links[k] = info
                version_plan = self._prepare_versions_for_migration(
                    pre_move_links,
                    source_project,
                    target_project,
                    version_prefix_strip,
                    dry_run=True,
                    include_released_versions=migrate_released_versions,
                )
                return {
                    "mapping": {},
                    "dry_run": True,
                    "would_move_keys": all_keys,
                    "payload_summary": {
                        "newProjectKey": target_project,
                        "issueCount": len(all_keys),
                    },
                    "audit": {
                        "selection": {
                            "from_jql_count": len(issues_data),
                            "selected_count": len(candidate_keys),
                            "skipped_closed_count": skipped_closed,
                            "skipped_other_count": skipped_other,
                            "skip_reasons": selection_skip_reasons,
                            "selected_issues": selected_audit,
                            "skipped_issues": skipped_audit,
                        },
                        "versions": version_plan,
                    },
                }
            pre_move_links = {}
            for k in all_keys:
                info = self.get_issue_for_migration(k, include_closed=True)
                if info:
                    pre_move_links[k] = info
            all_keys_set = set(all_keys)
            # Include any direct parent not in batch so we can move parent first, then subtask (API requirement)
            expanded = True
            while expanded:
                expanded = False
                for k in list(all_keys_set):
                    info = pre_move_links.get(k)
                    if not info or not info.get("parent"):
                        continue
                    parent_key = info["parent"]
                    if parent_key in all_keys_set:
                        continue
                    all_keys_set.add(parent_key)
                    all_keys.append(parent_key)
                    if parent_key not in pre_move_links:
                        pre_move_links[parent_key] = self.get_issue_for_migration(parent_key, include_closed=True) or {}
                    expanded = True
                    print(f"Included missing parent {parent_key} (parent of {k}) in batch")
            epic_parents = set()
            for k in all_keys:
                epic_link = (pre_move_links.get(k) or {}).get("epic_link")
                if epic_link:
                    epic_parents.add(epic_link)
            epics_not_in_batch = epic_parents - all_keys_set
            for epic_key in epics_not_in_batch:
                if epic_key not in pre_move_links:
                    pre_move_links[epic_key] = self.get_issue_for_migration(epic_key, include_closed=True) or {}
            version_plan = self._prepare_versions_for_migration(
                pre_move_links,
                source_project,
                target_project,
                version_prefix_strip,
                dry_run=False,
                include_released_versions=migrate_released_versions,
            )
            version_name_to_id = version_plan["name_to_target_id"]
            for epic_key in epics_not_in_batch:
                new_epic_key = self._copy_epic_to_target(epic_key, target_project, component_map)
                if new_epic_key:
                    mapping[epic_key] = new_epic_key
                    self._create_issue_link(new_epic_key, epic_key, "Relates to")
            # Bulk move API requires parent/epic to be moved first or target parent specified for children
            # "Parent" = direct parent (subtask) or Epic Link when epic is in batch
            key_to_effective_parent: Dict[str, str] = {}
            for k in all_keys:
                info = pre_move_links.get(k)
                if not info:
                    continue
                p = info.get("parent")
                if p and p in all_keys_set:
                    key_to_effective_parent[k] = p
                else:
                    el = info.get("epic_link")
                    if el and el in all_keys_set:
                        key_to_effective_parent[k] = el
            # Debug: print hierarchy so we can see structure when API fails
            print(f"Bulk move: {len(all_keys)} issues. Hierarchy:")
            for k in all_keys:
                info = pre_move_links.get(k) or {}
                p = info.get("parent")
                el = info.get("epic_link")
                eff = key_to_effective_parent.get(k)
                typ = info.get("type", "?")
                print(f"  {k} type={typ} parent={p} epic_link={el} effective_parent={eff}")
            moved = set()
            # Two-stage move: parents (Epic/Story/Bug etc.) first, then Sub-tasks. This avoids the API
            # auto-including subtasks when moving a parent, which would apply only the parent mapping's
            # targetMandatoryFields and fail subtasks that require e.g. "Affects versions".
            non_subtask = [k for k in all_keys if (pre_move_links.get(k) or {}).get("type") != "Sub-task"]
            subtask_keys = [k for k in all_keys if (pre_move_links.get(k) or {}).get("type") == "Sub-task"]
            phase = 0
            # Stage 1: move non-Sub-task issues in waves (roots, then children)
            remaining = list(non_subtask)
            while remaining:
                roots = [k for k in remaining if key_to_effective_parent.get(k) not in all_keys_set]
                if roots:
                    phase += 1
                    print(f"Phase {phase} (roots): moving {len(roots)} issues (no parent in batch): {roots[:20]}{'...' if len(roots) > 20 else ''}")
                    mapping.update(
                        self._bulk_move_issues(
                            roots,
                            target_project,
                            infer_field_defaults=infer_field_defaults,
                            infer_status_defaults=infer_status_defaults,
                            infer_classification_defaults=infer_classification_defaults,
                            target_mandatory_fields=bulk_move_target_mandatory_fields,
                        )
                    )
                    moved.update(roots)
                    remaining = [k for k in remaining if k not in moved]
                    continue
                next_batch = [k for k in remaining if key_to_effective_parent.get(k) in mapping]
                if not next_batch:
                    break
                # When target has mandatory fields (e.g. Affects versions), move one parent at a time.
                # The bulk move API auto-includes a parent's subtasks in the same request but only
                # applies targetMandatoryFields to the parent mapping, so subtasks fail. One parent
                # per request avoids one huge request; if the API still includes that parent's
                # subtasks we will still fail and the target project's field config must match IS.
                batches = (
                    [[k] for k in next_batch]
                    if bulk_move_target_mandatory_fields
                    else [next_batch]
                )
                for batch in batches:
                    key_to_target_parent = {c: mapping[key_to_effective_parent[c]] for c in batch}
                    phase += 1
                    print(f"Phase {phase} (parents): moving {len(batch)} issues with target parent: {dict(list(key_to_target_parent.items())[:15])}{'...' if len(key_to_target_parent) > 15 else ''}")
                    mapping.update(
                        self._bulk_move_issues(
                            batch,
                            target_project,
                            infer_field_defaults=infer_field_defaults,
                            infer_status_defaults=infer_status_defaults,
                            infer_classification_defaults=infer_classification_defaults,
                            target_mandatory_fields=bulk_move_target_mandatory_fields,
                            key_to_target_parent=key_to_target_parent,
                        )
                    )
                    moved.update(batch)
                remaining = [k for k in remaining if k not in moved]
            # Stage 2: move Sub-tasks (their parents are already in mapping)
            remaining = [k for k in subtask_keys if k not in moved]
            while remaining:
                next_batch = [k for k in remaining if key_to_effective_parent.get(k) in mapping]
                if not next_batch:
                    raise RuntimeError(
                        f"Cannot move {len(remaining)} subtasks: parent not in mapping. "
                        f"Example keys: {remaining[:10]}. Effective parents: {[key_to_effective_parent.get(k) for k in remaining[:5]]}"
                    )
                key_to_target_parent = {c: mapping[key_to_effective_parent[c]] for c in next_batch}
                phase += 1
                print(f"Phase {phase} (subtasks): moving {len(next_batch)} issues with target parent: {dict(list(key_to_target_parent.items())[:15])}{'...' if len(key_to_target_parent) > 15 else ''}")
                mapping.update(
                    self._bulk_move_issues(
                        next_batch,
                        target_project,
                        infer_field_defaults=infer_field_defaults,
                        infer_status_defaults=infer_status_defaults,
                        infer_classification_defaults=infer_classification_defaults,
                        target_mandatory_fields=bulk_move_target_mandatory_fields,
                        key_to_target_parent=key_to_target_parent,
                    )
                )
                moved.update(next_batch)
                remaining = [k for k in remaining if k not in moved]
            moved_with_target_parent = {k for k in all_keys if key_to_effective_parent.get(k) in all_keys_set}
            self._apply_versions_after_move(mapping, pre_move_links, version_name_to_id)
            if comment_previous_key:
                for old_key, new_key in mapping.items():
                    try:
                        self._add_comment(new_key, f"Moved from {old_key}")
                    except Exception as e:
                        print(f"  - Warning: could not add comment to {new_key}: {e}")
            self._recreate_issue_links_after_migration(mapping, pre_move_links=pre_move_links)
            for old_key, new_key in mapping.items():
                if old_key in epics_not_in_batch:
                    continue
                if old_key in moved_with_target_parent:
                    continue
                epic_link = (pre_move_links.get(old_key) or {}).get("epic_link")
                if epic_link and epic_link in mapping:
                    self._set_epic_link(new_key, mapping[epic_link])
        else:
            copy_links: Dict[str, Dict[str, Any]] = {}
            for key in candidate_keys:
                full = self.get_issue_for_migration(key, include_closed=migrate_closed)
                if not full:
                    continue
                comps = full.get("components") or []
                if component_filter and not any(c in component_filter for c in comps):
                    continue
                if summary_include and summary_include not in (full.get("summary") or ""):
                    continue
                copy_links[key] = full
            version_plan = self._prepare_versions_for_migration(
                copy_links,
                source_project,
                target_project,
                version_prefix_strip,
                dry_run=dry_run,
                include_released_versions=migrate_released_versions,
            )
            version_name_to_id = version_plan["name_to_target_id"]
            for key in candidate_keys:
                full = copy_links.get(key)
                if not full:
                    continue
                comps = full.get("components") or []
                create_payload: Dict[str, Any] = {
                    "project": target_project,
                    "summary": full["summary"],
                    "type": full.get("type", "Task"),
                    "description": full.get("description"),
                    "priority": full.get("priority"),
                    "labels": full.get("labels"),
                    "components": [component_map.get(c, c) for c in comps],
                }
                if full.get("assignee"):
                    create_payload["assignee"] = full["assignee"]
                fix_ids = []
                for v in full.get("fix_versions") or []:
                    tid = version_name_to_id.get((v.get("name") or "").strip())
                    if tid:
                        fix_ids.append(tid)
                aff_ids = []
                for v in full.get("affects_versions") or []:
                    tid = version_name_to_id.get((v.get("name") or "").strip())
                    if tid:
                        aff_ids.append(tid)
                if fix_ids:
                    create_payload["fixVersions"] = fix_ids
                if aff_ids:
                    create_payload["versions"] = aff_ids
                if dry_run:
                    mapping[key] = f"(would create in {target_project})"
                    continue
                created = self.create_issues([create_payload], dry_run=False)
                if created and created[0].get("status") == "success":
                    new_key = created[0]["issue_key"]
                    mapping[key] = new_key
                    self._create_issue_link(new_key, key, "Relates to")
                    if comment_on_source:
                        try:
                            self._add_comment(key, f"Migrated to {new_key}")
                        except Exception:
                            pass
            self._recreate_issue_links_after_migration(mapping)

        out = {"mapping": mapping}
        if dry_run:
            out["audit"] = {
                "selection": {
                    "from_jql_count": len(issues_data),
                    "selected_count": len(candidate_keys),
                    "skipped_closed_count": skipped_closed,
                    "skipped_other_count": skipped_other,
                    "skip_reasons": selection_skip_reasons,
                    "selected_issues": selected_audit,
                    "skipped_issues": skipped_audit,
                }
            }
            if "version_plan" in locals():
                out["audit"]["versions"] = version_plan
        if mapping_file_path and mapping:
            with open(mapping_file_path, "w") as f:
                json.dump(mapping, f, indent=2)
            out["mapping_file"] = mapping_file_path
        return out

    def _recreate_issue_links_after_migration(
        self, mapping: Dict[str, str], pre_move_links: Optional[Dict[str, Dict]] = None
    ) -> None:
        """Recreate issue links between migrated issues using old_key -> new_key mapping.
        For move, pass pre_move_links (key -> get_issue_for_migration result) captured before move."""
        for old_in, new_in in mapping.items():
            full = (pre_move_links or {}).get(old_in) or self.get_issue_for_migration(old_in, include_closed=True)
            if not full:
                continue
            for link in full.get("issuelinks", []):
                other_old = link.get("outward") or link.get("inward")
                if not other_old or other_old not in mapping:
                    continue
                other_new = mapping[other_old]
                try:
                    self._create_issue_link(new_in, other_new, link.get("type", "Relates to"))
                except Exception:
                    pass

    def transition_issue(self, issue: Any, target_status: str, **kwargs: Any) -> None:
        """Backward-compatible wrapper around transition_to_status."""
        result = self.transition_to_status(issue.key, target_status, **kwargs)
        if result.get("status") == "error":
            raise JiraHttpError(None, result.get("message", "Transition failed"), result)

    def get_full_issue_for_validation(self, issue_key: str) -> Optional[Dict[str, Any]]:
        """Fetch issue with components and labels for validation (does not skip by status)."""
        return self.get_issue_for_migration(issue_key, include_closed=True)

    def validate_migration(self, mapping: Dict[str, str]) -> Dict[str, Any]:
        """
        Validate that migrated issues match source. For move, source no longer exists so we
        only verify target issue exists and has key fields. For copy, compare source vs target.
        Returns summary and per-issue results with diffs.
        """
        results = []
        for source_key, target_key in mapping.items():
            if target_key.startswith("(would create"):
                results.append({"source_key": source_key, "target_key": target_key, "ok": True, "diffs": [], "note": "dry_run"})
                continue
            try:
                target_full = self.get_full_issue_for_validation(target_key)
                if not target_full:
                    results.append({
                        "source_key": source_key, "target_key": target_key, "ok": False,
                        "diffs": [{"field": "exists", "source_value": "N/A", "target_value": "Issue not found"}],
                    })
                    continue
                source_full = self.get_full_issue_for_validation(source_key)
                if not source_full:
                    results.append({
                        "source_key": source_key, "target_key": target_key, "ok": True,
                        "diffs": [], "note": "Source no longer exists (moved); target exists",
                    })
                    continue
                diffs = []
                for field in ("summary", "type", "priority", "status"):
                    s_val = source_full.get(field)
                    t_val = target_full.get(field)
                    if s_val != t_val:
                        diffs.append({"field": field, "source_value": s_val, "target_value": t_val})
                s_comp = set(source_full.get("components") or [])
                t_comp = set(target_full.get("components") or [])
                if s_comp != t_comp:
                    diffs.append({"field": "components", "source_value": list(s_comp), "target_value": list(t_comp)})
                s_lab = set(source_full.get("labels") or [])
                t_lab = set(target_full.get("labels") or [])
                if s_lab != t_lab:
                    diffs.append({"field": "labels", "source_value": list(s_lab), "target_value": list(t_lab)})
                s_sub = len(source_full.get("subtasks") or [])
                t_sub = len(target_full.get("subtasks") or [])
                if s_sub != t_sub:
                    diffs.append({"field": "subtasks_count", "source_value": s_sub, "target_value": t_sub})
                results.append({"source_key": source_key, "target_key": target_key, "ok": len(diffs) == 0, "diffs": diffs})
            except Exception as e:
                results.append({
                    "source_key": source_key, "target_key": target_key, "ok": False,
                    "diffs": [{"field": "error", "source_value": None, "target_value": str(e)}],
                })
        total = len(results)
        mismatches = sum(1 for r in results if not r.get("ok", True))
        return {"summary": f"{total} issues validated, {mismatches} mismatches", "results": results}

def main():
    if len(sys.argv) < 2:
        print("Usage: python jira_dominator.py <command> [args]")
        print("Commands:")
        print("  report <issue_key>       - Generate a report for the specified issue")
        print("  update <json_file>       - Update issues from a JSON file")
        print("  create <json_file>       - Create new issues from a JSON file")
        print("  migrate <config_file>    - Migrate issues to another project")
        print("  validate-migration <mapping_file> - Validate migration mapping")
        print("  diagnose                     - Show API identity and sample search (for permission debugging)")
        sys.exit(1)
    command = sys.argv[1].lower()
    dry_run = '--dry-run' in sys.argv
    args = [a for a in sys.argv[2:] if a != '--dry-run']
    dominator = JiraDominator()
    try:
        if command == "report" and len(args) == 1:
            issue_key = args[0].upper()
            issues = dominator.get_related_issues(issue_key)
            markdown_content = dominator.generate_markdown(issues)
            output_file = f"{issue_key}_report.md"
            with open(output_file, 'w') as f:
                f.write(markdown_content)
            print(f"Report generated successfully: {output_file}")
        elif command == "update" and len(args) == 1:
            with open(args[0], 'r') as f:
                updates = json.load(f)
            results = dominator.update_issues(updates, dry_run=dry_run)
            for result in results:
                status_symbol = "✓" if result['status'] == 'success' else ("○" if result['status'] == 'dry_run' else "✗")
                print(f"{status_symbol} {result['issue_key']}: {result['message']}")
        elif command == "create" and len(args) == 1:
            with open(args[0], 'r') as f:
                issues_to_create = json.load(f)
            results = dominator.create_issues(issues_to_create, dry_run=dry_run)
            for result in results:
                status_symbol = "✓" if result['status'] == 'success' else ("○" if result['status'] == 'dry_run' else "✗")
                print(f"{status_symbol} {result.get('issue_key', 'N/A')}: {result['message']}")
        elif command == "migrate" and len(args) == 1:
            with open(args[0], 'r') as f:
                config = json.load(f)
            mapping_path = config.get('mapping_file') or 'migration_mapping.json'
            result = dominator.migrate_issues(
                source_project=config['source_project'],
                target_project=config['target_project'],
                jql=config['jql'],
                options=config.get('options', {}),
                dry_run=dry_run,
                mapping_file_path=mapping_path,
            )
            if result.get('message'):
                print(result['message'])
            if result.get('dry_run'):
                keys = result.get('would_move_keys', [])
                summary = result.get('payload_summary', {})
                print(f"Dry run: would move {len(keys)} issue(s) to project {summary.get('newProjectKey', '?')}")
                audit = result.get("audit") or {}
                selection = audit.get("selection") or {}
                if selection:
                    selected_reasons = {}
                    for item in (selection.get("selected_issues") or []):
                        reason = item.get("reason", "unknown")
                        selected_reasons[reason] = selected_reasons.get(reason, 0) + 1
                    print(
                        "Selection audit:"
                        f" from_jql={selection.get('from_jql_count', 0)},"
                        f" selected={selection.get('selected_count', 0)},"
                        f" skipped_closed={selection.get('skipped_closed_count', 0)},"
                        f" skipped_other={selection.get('skipped_other_count', 0)}"
                    )
                    for reason, count in selected_reasons.items():
                        print(f"  - selected[{reason}] = {count}")
                    for reason, count in (selection.get("skip_reasons") or {}).items():
                        print(f"  - skipped[{reason}] = {count}")
                versions_audit = audit.get("versions") or {}
                if versions_audit:
                    existing_matches = versions_audit.get("existing_matches") or []
                    would_create_versions = versions_audit.get("would_create_versions") or []
                    mapped = versions_audit.get("name_to_target_id") or {}
                    unmapped = versions_audit.get("unmapped_source_names") or []
                    skipped_released = versions_audit.get("skipped_released_versions") or []
                    print(
                        "Version audit:"
                        f" existing={len(existing_matches)},"
                        f" would_create={len(would_create_versions)},"
                        f" mapped={len(mapped)},"
                        f" unmapped={len(unmapped)},"
                        f" skipped_released={len(skipped_released)}"
                    )
                    if would_create_versions:
                        print("  - would create versions:")
                        for item in would_create_versions[:50]:
                            print(f"    * {item.get('source_name')} -> {item.get('target_name')}")
                    if existing_matches:
                        print("  - existing version matches:")
                        for item in existing_matches[:50]:
                            print(f"    * {item.get('source_name')} -> {item.get('target_name')} (id={item.get('target_id')})")
                    if unmapped:
                        print("  - unmapped source versions:")
                        for name in unmapped[:50]:
                            print(f"    * {name}")
                    if skipped_released:
                        print("  - skipped released source versions:")
                        for name in skipped_released[:50]:
                            print(f"    * {name}")
                else:
                    print("Version audit: no version data detected from selected issues.")
                for k in keys:
                    print(f"  {k}")
            if result.get('mapping_file'):
                print(f"Mapping written to {result['mapping_file']}")
            for k, v in result.get('mapping', {}).items():
                print(f"  {k} -> {v}")
        elif command == "validate-migration" and len(args) == 1:
            with open(args[0], 'r') as f:
                mapping = json.load(f)
            validation = dominator.validate_migration(mapping)
            print(json.dumps(validation, indent=2))
        elif command == "diagnose":
            url = f"{dominator.jira_url}/rest/api/3/myself"
            r = requests.get(url, auth=(dominator.jira_email, dominator.jira_token), headers={"Accept": "application/json"})
            if r.status_code != 200:
                print(f"Auth check (/myself): {r.status_code}")
                if r.status_code == 401:
                    print("  -> Token not accepted. Generate a new API token at id.atlassian.com/manage-profile/security/api-tokens")
                    print("  -> Ensure .env has JIRA_EMAIL and JIRA_API_TOKEN with no trailing spaces/newlines.")
                return
            u = r.json()
            print("API identity:", u.get("emailAddress") or u.get("displayName") or u.get("accountId"), f"(accountId: {u.get('accountId')})")
            jql = 'project = "IS"'
            try:
                res = dominator.search_issues_direct(jql, max_results=10)
                issues = res.get("issues") or res.get("values") or []
                print(f"Search '{jql}': {len(issues)} issues (max 10 requested)")
                if len(issues) == 0:
                    print("  -> If the same JQL returns 300+ in the Jira UI, this user may not have Browse access to project IS.")
            except Exception as e:
                print(f"Search failed: {e}")
        else:
            print("Invalid command or arguments")
            sys.exit(1)
    except Exception as e:
        print(f"Error: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()