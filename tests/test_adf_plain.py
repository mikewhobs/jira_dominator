#!/usr/bin/env python3
"""Unit tests for ADF/HTML helpers and structured Jira errors (no Jira network)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jira_dominator import (
    JiraDominator,
    adf_to_plain_text,
    format_jira_exception,
    html_to_plain,
    JiraHttpError,
)


class TestAdfToPlainText(unittest.TestCase):
    def test_heading_and_paragraph(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 2},
                    "content": [{"type": "text", "text": "Section"}],
                },
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Hello "},
                        {"type": "text", "text": "world"},
                    ],
                },
            ],
        }
        t = adf_to_plain_text(adf)
        self.assertIn("Section", t)
        self.assertIn("Hello", t)
        self.assertIn("world", t)

    def test_bullet_list(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "First"}],
                                }
                            ],
                        },
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Second"}],
                                }
                            ],
                        },
                    ],
                }
            ],
        }
        t = adf_to_plain_text(adf)
        self.assertIn("- First", t)
        self.assertIn("- Second", t)

    def test_empty_and_non_doc(self):
        self.assertEqual(adf_to_plain_text(None), "")
        self.assertEqual(adf_to_plain_text({}), "")


class TestHtmlToPlain(unittest.TestCase):
    def test_strips_tags(self):
        self.assertEqual(html_to_plain("<p>Hello</p>"), "Hello")


class TestFormatJiraException(unittest.TestCase):
    def test_jira_http_error(self):
        err = JiraHttpError(403, "Forbidden", {"errorMessages": ["No permission"]})
        d = format_jira_exception(err)
        self.assertEqual(d["error"], "jira_api_error")
        self.assertEqual(d["status_code"], 403)
        self.assertEqual(d["details"], {"errorMessages": ["No permission"]})


class TestCustomFieldValueSimplification(unittest.TestCase):
    """JSM error blobs + ADF in custom fields (no Jira __init__)."""

    def setUp(self):
        self.dom = object.__new__(JiraDominator)
        self.dom._fields_by_id_cache = {
            "customfield_10000": {"name": "Steps to reproduce"},
            "customfield_10001": {"name": "JSM noise"},
        }

    def test_adf_custom_field_becomes_plain(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Repro here"}],
                }
            ],
        }
        self.assertEqual(self.dom._simplify_field_value(adf), "Repro here")

    def test_jsm_error_blob_shortened(self):
        blob = {
            "errorMessage": "The service space does not exist",
            "i18nErrorMessage": {"key": "x"},
        }
        v = self.dom._simplify_field_value(blob)
        self.assertTrue(v.startswith("[unavailable]"))
        self.assertIn("service space", v)

    def test_impure_error_shape_not_treated_as_blob(self):
        d = {"errorMessage": "x", "other": 1}
        v = self.dom._simplify_field_value(d)
        self.assertIsInstance(v, dict)

    def test_build_custom_fields_map_adf_and_blob(self):
        fields_raw = {
            "customfield_10000": {
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "One step"}],
                    }
                ],
            },
            "customfield_10001": {
                "errorMessage": "Service space does not exist",
                "i18nErrorMessage": {},
            },
        }
        m = self.dom._build_custom_fields_map(fields_raw, None)
        self.assertEqual(m["customfield_10000"]["value"], "One step")
        self.assertIn("Service space", m["customfield_10001"]["value"])


class TestGenerateMarkdownSmoke(unittest.TestCase):
    """Markdown report uses the same issue dict shape as get_issue_details."""

    def test_links_and_attachments_surface_in_markdown(self):
        dom = object.__new__(JiraDominator)
        issues = [
            {
                "key": "TEST-1",
                "summary": "Smoke ticket",
                "type": "Bug",
                "status": "Open",
                "priority": "High",
                "assignee": "alice",
                "reporter": "bob",
                "created": "2026-01-01T00:00:00.000+0000",
                "updated": "2026-01-02T00:00:00.000+0000",
                "description_plain": "Repro body",
                "description": "Repro body",
                "description_rendered": None,
                "subtasks": [],
                "child_issues": [],
                "issuelinks": [
                    {"type": "Relates", "inward": "TEST-2", "outward": None},
                ],
                "parent": None,
                "attachments": [
                    {
                        "filename": "screenshot.png",
                        "size": 1024,
                        "mimeType": "image/png",
                        "created": "2026-01-01",
                        "content": "https://example.invalid/rest/api/3/attachment/1",
                    },
                ],
                "custom_fields": {
                    "customfield_1": {"name": "Env", "value": "prod"},
                },
                "comments": {"comments": [], "total": 0},
            }
        ]
        md = dom.generate_markdown(issues)
        self.assertIn("TEST-1", md)
        self.assertIn("Smoke ticket", md)
        self.assertIn("Repro body", md)
        self.assertIn("TEST-2", md)
        self.assertIn("Relates", md)
        self.assertIn("screenshot.png", md)
        self.assertIn("1024", md)
        self.assertIn("Env", md)
        self.assertIn("prod", md)


class TestTransitionHelpers(unittest.TestCase):
    def setUp(self):
        self.dom = object.__new__(JiraDominator)

    def test_pick_transition_by_target_status_not_button_name(self):
        transitions = [
            {"id": "1", "name": "Submit to QA", "to": {"name": "Ready for Testing"}},
            {"id": "2", "name": "Start Progress", "to": {"name": "In Progress"}},
        ]
        picked = self.dom._pick_transition_to_status(transitions, "Ready for Testing")
        self.assertIsNotNone(picked)
        self.assertEqual(picked["name"], "Submit to QA")
        self.assertEqual(picked["to"]["name"], "Ready for Testing")

    def test_pick_next_hop_on_default_path(self):
        transitions = [
            {"id": "2", "name": "Submit to QA", "to": {"name": "Ready for Testing"}},
            {"id": "3", "name": "Start Progress", "to": {"name": "In Progress"}},
        ]
        picked = self.dom._pick_next_transition_on_path(
            transitions, "In Progress", "Ready for Testing"
        )
        self.assertEqual(picked["to"]["name"], "Ready for Testing")

    def test_transition_target_name_string_to(self):
        self.assertEqual(
            self.dom._transition_target_name({"to": "Ready for Testing"}),
            "Ready for Testing",
        )

    def test_normalize_worklog_string(self):
        wl = self.dom._normalize_worklog_input("30m")
        self.assertEqual(wl["time_spent"], "30m")

    def test_change_notes_deferred_when_not_on_screen(self):
        transition = {
            "id": "1",
            "name": "On Hold",
            "to": {"name": "On Hold"},
            "fields": {},
        }
        fields, deferred = self.dom._build_transition_fields_for_screen(
            transition, change_notes="Waiting on vendor"
        )
        self.assertNotIn(self.dom.CHANGE_NOTES_FIELD, fields)
        self.assertEqual(deferred, "Waiting on vendor")

    def test_change_notes_on_screen_when_field_present(self):
        transition = {
            "id": "2",
            "name": "Submit to QA",
            "to": {"name": "Ready for Testing"},
            "fields": {self.dom.CHANGE_NOTES_FIELD: {"required": True, "schema": {"type": "string"}}},
        }
        fields, deferred = self.dom._build_transition_fields_for_screen(
            transition, change_notes="Shipped fix"
        )
        self.assertIn(self.dom.CHANGE_NOTES_FIELD, fields)
        self.assertIsNone(deferred)

    def test_auto_worklog_when_submit_to_qa(self):
        transition = {
            "id": "2",
            "name": "Submit to QA",
            "to": {"name": "Ready for Testing"},
            "fields": {},
        }
        self.assertTrue(self.dom._transition_requires_worklog(transition))

    def test_transition_worklog_uses_plain_comment(self):
        wl = self.dom._build_worklog_add("15m", "logged time", plain_comment=True)
        self.assertEqual(wl["add"]["comment"], "logged time")
        self.assertIsInstance(wl["add"]["comment"], str)

    def test_build_worklog_update_uses_adf_comment(self):
        wl = self.dom._build_worklog_add("30m", "Shipped fix")
        self.assertEqual(wl["add"]["timeSpent"], "30m")
        self.assertEqual(wl["add"]["comment"]["type"], "doc")


class TestCoerceDescription(unittest.TestCase):
    def setUp(self):
        self.dom = object.__new__(JiraDominator)

    def test_plain_string_becomes_adf(self):
        adf = self.dom.coerce_description_to_adf("Simple body")
        self.assertEqual(adf["type"], "doc")
        self.assertIn("Simple body", adf["content"][0]["content"][0]["text"])

    def test_partial_structured_description(self):
        adf = self.dom.coerce_description_to_adf({"purpose": "Do the thing"})
        text = adf_to_plain_text(adf)
        self.assertIn("Do the thing", text)


class TestVersionUpdateFields(unittest.TestCase):
    def setUp(self):
        self.dom = object.__new__(JiraDominator)

    def test_merge_version_ids_set(self):
        self.assertEqual(self.dom._merge_version_ids(["1", "2"], ["3"], "set"), ["3"])

    def test_merge_version_ids_add(self):
        self.assertEqual(self.dom._merge_version_ids(["1", "2"], ["2", "3"], "add"), ["1", "2", "3"])

    def test_merge_version_ids_remove(self):
        self.assertEqual(self.dom._merge_version_ids(["1", "2", "3"], ["2"], "remove"), ["1", "3"])

    def test_build_version_update_fields_set_fix(self):
        issue = type("Issue", (), {})()
        fix_v = type("V", (), {"id": "100"})()
        issue.fields = type(
            "F",
            (),
            {
                "project": type("P", (), {"key": "IS"})(),
                "fixVersions": [fix_v],
                "versions": [],
            },
        )()
        self.dom._resolve_version_ids = lambda _p, refs: [str(r) for r in refs]
        fields, audit = self.dom._build_version_update_fields(
            issue,
            "IS-1",
            {"fix_versions": ["200"], "fix_versions_mode": "set"},
        )
        self.assertEqual(fields["fixVersions"], [{"id": "200"}])
        self.assertEqual(audit["fix_versions"]["mode"], "set")


class TestSprintResolution(unittest.TestCase):
    def setUp(self):
        self.dom = object.__new__(JiraDominator)

    def test_none_and_backlog_resolve_to_none(self):
        self.assertIsNone(self.dom._resolve_sprint_id("IS", None))
        self.assertIsNone(self.dom._resolve_sprint_id("IS", ""))
        self.assertIsNone(self.dom._resolve_sprint_id("IS", "backlog"))

    def test_numeric_ref_resolves_directly_without_api(self):
        def _fail(*a, **k):
            raise AssertionError("should not hit the API for numeric sprint refs")

        self.dom._api_json = _fail
        self.assertEqual(self.dom._resolve_sprint_id("IS", 42), 42)
        self.assertEqual(self.dom._resolve_sprint_id("IS", "42"), 42)

    def test_name_resolves_via_boards_and_sprints(self):
        calls = []

        def fake_api(method, path, params=None, payload=None):
            calls.append(path)
            if path == "/rest/agile/1.0/board":
                return {"values": [{"id": 5, "name": "IS board"}]}
            if path == "/rest/agile/1.0/board/5/sprint":
                return {
                    "isLast": True,
                    "values": [
                        {"id": 100, "name": "Sprint 6"},
                        {"id": 101, "name": "Sprint 7"},
                    ],
                }
            raise AssertionError(f"unexpected path {path}")

        self.dom._api_json = fake_api
        self.assertEqual(self.dom._resolve_sprint_id("IS", "Sprint 7"), 101)
        self.assertIn("/rest/agile/1.0/board", calls)

    def test_unknown_name_raises(self):
        self.dom._api_json = lambda *a, **k: {"values": [], "isLast": True}
        with self.assertRaises(ValueError):
            self.dom._resolve_sprint_id("IS", "Nonexistent Sprint")

    def test_move_to_sprint_uses_agile_endpoint(self):
        posts = []

        def fake_api(method, path, params=None, payload=None):
            posts.append((method, path, payload))
            return None

        self.dom._api_json = fake_api
        self.dom._resolve_sprint_id = lambda pk, ref: 55
        res = self.dom._move_issue_to_sprint("IS-1", "Sprint 7", project_key="IS")
        self.assertEqual(res, {"sprint": 55, "moved_to": "sprint"})
        self.assertEqual(
            posts[-1], ("POST", "/rest/agile/1.0/sprint/55/issue", {"issues": ["IS-1"]})
        )

    def test_move_to_backlog_when_sprint_none(self):
        posts = []

        def fake_api(method, path, params=None, payload=None):
            posts.append((method, path, payload))
            return None

        self.dom._api_json = fake_api
        self.dom._resolve_sprint_id = lambda pk, ref: None
        res = self.dom._move_issue_to_sprint("IS-1", None, project_key="IS")
        self.assertEqual(res, {"sprint": None, "moved_to": "backlog"})
        self.assertEqual(
            posts[-1], ("POST", "/rest/agile/1.0/backlog/issue", {"issues": ["IS-1"]})
        )


if __name__ == "__main__":
    unittest.main()
