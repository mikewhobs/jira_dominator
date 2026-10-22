#!/usr/bin/env python3
"""
Jira Dominator MCP Server

This server exposes Jira Dominator functionality as MCP tools.
"""

import asyncio
import json
import logging
from typing import Any, Sequence, Dict, List
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from jira_dominator import JiraDominator, format_jira_exception

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jira-mcp-server")

app = Server("jira-dominator")

# Global dominator instance
dominator = None

def init_dominator():
    """Initialize the JiraDominator instance."""
    global dominator
    if dominator is None:
        try:
            dominator = JiraDominator()
            logger.info("JiraDominator initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize JiraDominator: {e}")
            # Still create the dominator instance even if initial connection fails
            # This allows the server to start and connection can be retried later
            try:
                dominator = JiraDominator()
                logger.info("JiraDominator instance created (connection will be retried on first use)")
            except Exception:
                raise e


def _issue_detail_kwargs(arguments: dict) -> dict:
    """Map MCP args to get_issue_details / get_related_issues / generate_report kwargs."""
    return {
        "include_comments": arguments.get("include_comments", True),
        "max_comments": int(arguments.get("max_comments", 50)),
        "include_changelog": arguments.get("include_changelog", False),
        "extra_fields": arguments.get("extra_fields"),
    }


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available Jira tools."""
    return [
        Tool(
            name="jira_test_connection",
            description="Test connection to Jira and get current user info",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="jira_get_issue_details",
            description=(
                "Get detailed information about a Jira issue: summary, status, ADF description as "
                "description_plain / description_rendered (HTML) / description_adf, comments (default on), "
                "attachments, parent, issuelinks, subtasks, custom_fields (customfield_* plus optional extra_fields), "
                "optional changelog. Skipped/Done issues may return not-found."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "The Jira issue key (e.g., IS-1260)"
                    },
                    "include_comments": {
                        "type": "boolean",
                        "description": "If true, include first page of comments (plain + body_adf)",
                        "default": True
                    },
                    "max_comments": {
                        "type": "integer",
                        "description": "Max comments to fetch with the issue (paginate with jira_get_issue_comments)",
                        "default": 50
                    },
                    "include_changelog": {
                        "type": "boolean",
                        "description": "If true, include full changelog (large payload)",
                        "default": False
                    },
                    "extra_fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional field ids to include in custom_fields (e.g. environment)"
                    }
                },
                "required": ["issue_key"]
            }
        ),
        Tool(
            name="jira_get_issue_comments",
            description=(
                "List comments for an issue (ordered). Jira Cloud: GET /rest/api/3/issue/{key}/comment. "
                "Use start_at / max_results to paginate."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {"type": "string", "description": "Issue key"},
                    "start_at": {"type": "integer", "description": "Pagination offset (startAt)", "default": 0},
                    "max_results": {"type": "integer", "description": "Page size (maxResults)", "default": 50}
                },
                "required": ["issue_key"]
            }
        ),
        Tool(
            name="jira_search_issues",
            description=(
                "Search issues with JQL via POST /rest/api/3/search/jql. Pagination is token-based: "
                "pass next_page_token from the previous response to get the next page (not classic startAt offset)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "jql": {
                        "type": "string",
                        "description": "JQL query string"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results for this page (max 500)",
                        "default": 50
                    },
                    "next_page_token": {
                        "type": "string",
                        "description": "Continuation token from prior response (nextPageToken)"
                    },
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Issue field ids to return; default includes summary, status, description, assignee, etc."
                    }
                },
                "required": ["jql"]
            }
        ),
        Tool(
            name="jira_get_related_issues",
            description=(
                "Get all related issues (subtasks, linked issues, epic children). "
                "Uses the same enrichment options as jira_get_issue_details for each issue."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "The Jira issue key to get related issues for"
                    },
                    "include_comments": {
                        "type": "boolean",
                        "default": True
                    },
                    "max_comments": {"type": "integer", "default": 50},
                    "include_changelog": {"type": "boolean", "default": False},
                    "extra_fields": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": ["issue_key"]
            }
        ),
        Tool(
            name="jira_generate_report",
            description=(
                "Markdown report for an issue and all related issues: description (plain), comments, "
                "attachments, custom fields, links, subtasks. Same options as get_issue_details."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "The Jira issue key to generate report for"
                    },
                    "include_comments": {"type": "boolean", "default": True},
                    "max_comments": {"type": "integer", "default": 50},
                    "include_changelog": {"type": "boolean", "default": False},
                    "extra_fields": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": ["issue_key"]
            }
        ),
        Tool(
            name="jira_create_issues",
            description="Create new Jira issues from structured data",
            inputSchema={
                "type": "object",
                "properties": {
                    "issues": {
                        "type": "array",
                        "description": "Array of issue objects to create",
                        "items": {
                            "type": "object",
                            "properties": {
                                "project": {"type": "string", "description": "Project key"},
                                "summary": {"type": "string", "description": "Issue summary"},
                                "type": {"type": "string", "description": "Issue type (Task, Bug, Story, etc.)", "default": "Task"},
                                "description": {
                                    "description": "Plain string, ADF doc, or structured object (purpose, endpoint_details, criteria arrays)",
                                    "oneOf": [
                                        {"type": "string"},
                                        {
                                            "type": "object",
                                            "properties": {
                                                "purpose": {"type": "string"},
                                                "endpoint_details": {
                                                    "type": "object",
                                                    "properties": {
                                                        "method": {"type": "string"},
                                                        "path": {"type": "string"}
                                                    }
                                                },
                                                "success_criteria": {"type": "array", "items": {"type": "string"}},
                                                "failure_conditions": {"type": "array", "items": {"type": "string"}},
                                                "acceptance_criteria": {"type": "array", "items": {"type": "string"}}
                                            }
                                        }
                                    ]
                                },
                                "assignee": {"type": "string", "description": "Assignee accountId, email, or display name"},
                                "reporter": {"type": "string", "description": "Reporter (defaults to authenticated user)"},
                                "auto_reporter": {
                                    "type": "boolean",
                                    "description": "Set reporter to authenticated user when reporter omitted",
                                    "default": True
                                },
                                "priority": {"type": "string", "description": "Priority level"},
                                "labels": {"type": "array", "items": {"type": "string"}},
                                "components": {"type": "array", "items": {"type": "string"}},
                                "fix_versions": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Fix version names (resolved to ids)"
                                },
                                "affects_versions": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Affects version names"
                                },
                                "default_fix_version": {
                                    "type": "boolean",
                                    "description": "Use latest unreleased project version as fixVersion",
                                    "default": False
                                },
                                "epic_link": {"type": "string", "description": "Epic key to link to"},
                                "parent": {"type": "string", "description": "Parent issue key for subtasks"},
                                "sprint": {
                                    "description": "Sprint id (number) or active/future sprint name. Issue is moved into the sprint via the Agile API after creation.",
                                    "oneOf": [{"type": "string"}, {"type": "integer"}]
                                }
                            },
                            "required": ["project", "summary"]
                        }
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, simulate create without calling Jira",
                        "default": False
                    }
                },
                "required": ["issues"]
            }
        ),
        Tool(
            name="jira_update_issues",
            description=(
                "Update Jira issues. Transitions resolve by target status (to.name), not transition display name. "
                "Bundled worklog on status transitions satisfies validators requiring time logged on the same call. "
                "fix_versions / affects_versions support set (default), add, and remove modes for bulk version sweeps."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "updates": {
                        "type": "object",
                        "description": "Object mapping issue keys to update data",
                        "patternProperties": {
                            ".*": {
                                "type": "object",
                                "properties": {
                                    "summary": {"type": "string", "description": "New summary"},
                                    "description": {
                                        "description": "Plain string, ADF doc, or structured description object"
                                    },
                                    "assignee": {
                                        "description": "Assignee accountId/name, or null to unassign",
                                        "oneOf": [{"type": "string"}, {"type": "null"}]
                                    },
                                    "status": {
                                        "type": "string",
                                        "description": "Target workflow status name (e.g. Ready for Testing), not transition button label"
                                    },
                                    "comment": {"type": "string", "description": "Comment to add after update"},
                                    "change_notes": {"type": "string", "description": "Change notes (customfield_10047)"},
                                    "delivered_in_build": {
                                        "type": "string",
                                        "description": "Delivered in Build value (customfield_10056)"
                                    },
                                    "worklog": {
                                        "type": "object",
                                        "description": "Worklog bundled on status transition (or standalone if no status)",
                                        "properties": {
                                            "time_spent": {"type": "string", "description": "e.g. 30m, 1h"},
                                            "comment": {"type": "string", "description": "Worklog comment (plain text)"},
                                            "started": {"type": "string", "description": "ISO8601 start time (optional)"}
                                        },
                                        "required": ["time_spent"]
                                    },
                                    "fix_versions": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "Fix version names or ids (maps to fixVersions)"
                                    },
                                    "fix_versions_mode": {
                                        "type": "string",
                                        "enum": ["set", "add", "remove"],
                                        "description": "How to apply fix_versions: replace (set), merge (add), or strip (remove)",
                                        "default": "set"
                                    },
                                    "affects_versions": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "Affects version names or ids (maps to versions field)"
                                    },
                                    "affects_versions_mode": {
                                        "type": "string",
                                        "enum": ["set", "add", "remove"],
                                        "description": "How to apply affects_versions",
                                        "default": "set"
                                    },
                                    "project": {
                                        "type": "string",
                                        "description": "Optional project key for resolving version names (defaults from issue key)"
                                    },
                                    "sprint": {
                                        "description": "Sprint id or active/future sprint name to move the issue into; null (or 'backlog') moves it to the backlog. Uses the Agile API, so it works even when the Sprint field is not on the edit screen.",
                                        "oneOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]
                                    }
                                }
                            }
                        }
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, simulate update without calling Jira",
                        "default": False
                    }
                },
                "required": ["updates"]
            }
        ),
        Tool(
            name="jira_log_work",
            description="Log time on a Jira issue via POST /rest/api/3/issue/{key}/worklog",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {"type": "string", "description": "Issue key"},
                    "time_spent": {"type": "string", "description": "Duration e.g. 30m, 1h 15m"},
                    "comment": {"type": "string", "description": "Worklog comment (plain text)"},
                    "started": {"type": "string", "description": "ISO8601 start datetime (optional)"},
                    "dry_run": {"type": "boolean", "default": False}
                },
                "required": ["issue_key", "time_spent"]
            }
        ),
        Tool(
            name="jira_link_issues",
            description="Create an issue link (Relates, Blocks, Clones, etc.) between two issues",
            inputSchema={
                "type": "object",
                "properties": {
                    "inward_key": {"type": "string", "description": "Inward issue key"},
                    "outward_key": {"type": "string", "description": "Outward issue key"},
                    "link_type": {
                        "type": "string",
                        "description": "Link type name as configured in Jira",
                        "default": "Relates"
                    },
                    "dry_run": {"type": "boolean", "default": False}
                },
                "required": ["inward_key", "outward_key"]
            }
        ),
        Tool(
            name="jira_transition_issue",
            description=(
                "Walk an issue through workflow transitions to a target status. Matches transitions by "
                "destination status (to.name), discovers multi-hop paths, and bundles required transition "
                "fields plus optional worklog on the same REST transition call."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {"type": "string"},
                    "target_status": {
                        "type": "string",
                        "description": "Target status name (not transition button label)"
                    },
                    "change_notes": {"type": "string"},
                    "delivered_in_build": {"type": "string", "description": "customfield_10056"},
                    "worklog": {
                        "type": "object",
                        "properties": {
                            "time_spent": {"type": "string"},
                            "comment": {"type": "string"},
                            "started": {"type": "string"}
                        },
                        "required": ["time_spent"]
                    },
                    "dry_run": {"type": "boolean", "default": False}
                },
                "required": ["issue_key", "target_status"]
            }
        ),
        Tool(
            name="jira_get_priority_issues",
            description="Get high priority issues from specified projects",
            inputSchema={
                "type": "object",
                "properties": {
                    "projects": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of project keys to search",
                        "default": ["IS", "VRD"]
                    },
                    "priority": {
                        "type": "string",
                        "description": "Priority level to filter by",
                        "default": "Highest"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum results per project",
                        "default": 10
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="jira_migrate_issues",
            description="Migrate issues from source project to target project (move or copy)",
            inputSchema={
                "type": "object",
                "properties": {
                    "source_project": {"type": "string", "description": "Source project key"},
                    "target_project": {"type": "string", "description": "Target project key"},
                    "jql": {"type": "string", "description": "JQL to select issues (e.g. project = SOURCE)"},
                    "options": {
                        "type": "object",
                        "description": "Options: mode (move|copy), migrate_closed, migrate_closed_for_unreleased_versions, migrate_released_versions, exclude_closed_if_all_fixversions_released_and_missing_target, component_filter, summary_include, comment_previous_key, comment_on_source, component_map, version_prefix_strip, bulk_move_target_mandatory_fields",
                        "properties": {
                            "mode": {"type": "string", "enum": ["move", "copy"], "default": "move"},
                            "migrate_closed": {"type": "boolean", "default": False},
                            "migrate_closed_for_unreleased_versions": {
                                "type": "boolean",
                                "default": True,
                                "description": "When migrate_closed is false, include closed issues only if any fixVersion is unreleased."
                            },
                            "migrate_released_versions": {
                                "type": "boolean",
                                "default": True,
                                "description": "If false, do not create/map source versions that are released."
                            },
                            "exclude_closed_if_all_fixversions_released_and_missing_target": {
                                "type": "boolean",
                                "default": False,
                                "description": "If true, skip closed issues when all their fixVersions are released in source and none of the normalized target version names exist in target."
                            },
                            "component_filter": {"type": "array", "items": {"type": "string"}},
                            "summary_include": {"type": "string"},
                            "comment_previous_key": {"type": "boolean", "default": False},
                            "comment_on_source": {"type": "boolean", "default": False},
                            "component_map": {"type": "object", "additionalProperties": {"type": "string"}},
                            "version_prefix_strip": {
                                "type": "string",
                                "description": "Strip this prefix from version names when migrating. E.g. 'PREFIX - ' so 'PREFIX - 7.0.0' becomes '7.0.0'. Use '{target_project} - ' to use target project name as prefix."
                            },
                            "bulk_move_target_mandatory_fields": {"type": "object", "description": "Mandatory fields for bulk move (e.g. versions: [])"}
                        }
                    },
                    "dry_run": {"type": "boolean", "description": "If true, do not perform migration", "default": False}
                },
                "required": ["source_project", "target_project", "jql"]
            }
        ),
        Tool(
            name="jira_get_fields",
            description="Get all issue fields from Jira (field id, name, custom, schema). Use for custom field ID lookup and schema validation.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="jira_get_createmeta",
            description="Get create metadata for projects: required/allowed fields per issue type. Use for schema validation when creating issues.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_keys": {
                        "type": "string",
                        "description": "Comma-separated project keys to filter (e.g. IS,VRD). Optional."
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="jira_validate_migration",
            description="Validate migration by comparing source and target issues from a mapping",
            inputSchema={
                "type": "object",
                "properties": {
                    "mapping": {
                        "type": "object",
                        "description": "Object mapping source issue keys to target issue keys",
                        "additionalProperties": {"type": "string"}
                    }
                },
                "required": ["mapping"]
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    try:
        # Ensure dominator is initialized, retry if needed
        global dominator
        if dominator is None:
            init_dominator()
        # Test connection with a simple call if we haven't verified it yet
        try:
            dominator.jira.current_user()
        except Exception as conn_error:
            logger.warning(f"Connection test failed, reinitializing: {conn_error}")
            dominator = None
            init_dominator()
        
        if name == "jira_test_connection":
            user = dominator.jira.current_user()
            result = {"status": "connected", "user": user}
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        
        elif name == "jira_get_issue_details":
            issue_key = arguments["issue_key"]
            dk = _issue_detail_kwargs(arguments)
            result = dominator.get_issue_details(issue_key, **dk)
            if result:
                return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
            else:
                return [TextContent(type="text", text=json.dumps({"error": "Issue not found or skipped"}))]
        
        elif name == "jira_get_issue_comments":
            issue_key = arguments["issue_key"]
            result = dominator.get_issue_comments(
                issue_key,
                start_at=int(arguments.get("start_at", 0)),
                max_results=int(arguments.get("max_results", 50)),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
        
        elif name == "jira_search_issues":
            jql = arguments["jql"]
            max_results = int(arguments.get("max_results", 50))
            next_tok = arguments.get("next_page_token")
            fields_arg = arguments.get("fields")
            result = dominator.search_issues_direct(
                jql,
                max_results=max_results,
                next_page_token=next_tok,
                fields=fields_arg,
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
        
        elif name == "jira_get_related_issues":
            issue_key = arguments["issue_key"]
            dk = _issue_detail_kwargs(arguments)
            dominator.processed_issues.clear()
            result = dominator.get_related_issues(issue_key, **dk)
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
        
        elif name == "jira_generate_report":
            issue_key = arguments["issue_key"]
            dk = _issue_detail_kwargs(arguments)
            dominator.processed_issues.clear()
            issues = dominator.get_related_issues(issue_key, **dk)
            markdown_content = dominator.generate_markdown(issues)
            return [TextContent(type="text", text=markdown_content)]
        
        elif name == "jira_create_issues":
            issues = arguments["issues"]
            dry_run = arguments.get("dry_run", False)
            result = dominator.create_issues(issues, dry_run=dry_run)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        
        elif name == "jira_update_issues":
            updates = arguments["updates"]
            dry_run = arguments.get("dry_run", False)
            result = dominator.update_issues(updates, dry_run=dry_run)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "jira_log_work":
            result = dominator.log_work(
                arguments["issue_key"],
                arguments["time_spent"],
                comment=arguments.get("comment"),
                started=arguments.get("started"),
                dry_run=arguments.get("dry_run", False),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "jira_link_issues":
            result = dominator.link_issues(
                arguments["inward_key"],
                arguments["outward_key"],
                link_type=arguments.get("link_type", "Relates"),
                dry_run=arguments.get("dry_run", False),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "jira_transition_issue":
            result = dominator.transition_to_status(
                arguments["issue_key"],
                arguments["target_status"],
                change_notes=arguments.get("change_notes"),
                delivered_in_build=arguments.get("delivered_in_build"),
                worklog=arguments.get("worklog"),
                dry_run=arguments.get("dry_run", False),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        
        elif name == "jira_get_priority_issues":
            projects = arguments.get("projects", ["IS", "VRD"])
            priority = arguments.get("priority", "Highest")
            max_results = arguments.get("max_results", 10)
            
            result = {}
            for project in projects:
                try:
                    jql = f'project = "{project}" AND priority = "{priority}" ORDER BY updated DESC'
                    search_result = dominator.search_issues_direct(jql, max_results=max_results)
                    issues = search_result.get('issues', [])
                    
                    project_issues = []
                    for issue in issues:
                        fields = issue['fields']
                        issue_data = {
                            "key": issue['key'],
                            "summary": fields['summary'],
                            "status": fields['status']['name'],
                            "type": fields['issuetype']['name'],
                            "assignee": fields['assignee']['displayName'] if fields['assignee'] else 'Unassigned',
                            "updated": fields['updated']
                        }
                        project_issues.append(issue_data)
                    
                    result[project] = project_issues
                    
                except Exception as e:
                    result[project] = format_jira_exception(e)
            
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        
        elif name == "jira_migrate_issues":
            source_project = arguments["source_project"]
            target_project = arguments["target_project"]
            jql = arguments["jql"]
            options = arguments.get("options", {})
            dry_run = arguments.get("dry_run", False)
            result = dominator.migrate_issues(
                source_project=source_project,
                target_project=target_project,
                jql=jql,
                options=options,
                dry_run=dry_run,
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        
        elif name == "jira_validate_migration":
            mapping = arguments["mapping"]
            result = dominator.validate_migration(mapping)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        
        elif name == "jira_get_fields":
            fields = dominator.get_fields()
            return [TextContent(type="text", text=json.dumps(fields, indent=2))]
        
        elif name == "jira_get_createmeta":
            project_keys = arguments.get("project_keys")
            result = dominator.get_createmeta(project_keys=project_keys)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        
        else:
            return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
    
    except Exception as e:
        logger.error(f"Error in tool {name}: {e}")
        error_result = format_jira_exception(e)
        return [TextContent(type="text", text=json.dumps(error_result, indent=2, default=str))]

async def main():
    """Run the MCP server."""
    logger.info("Starting Jira Dominator MCP Server")
    try:
        # Try to initialize, but don't fail the server startup if connection fails
        try:
            init_dominator()
            logger.info("Jira connection verified")
        except Exception as e:
            logger.warning(f"Initial Jira connection failed, but server will start anyway: {e}")
            logger.info("Connection will be retried when tools are called")
        
        # Run the server
        async with stdio_server() as streams:
            await app.run(streams[0], streams[1], app.create_initialization_options())
            
    except Exception as e:
        logger.error(f"Failed to start server: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())