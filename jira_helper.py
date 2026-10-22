#!/usr/bin/env python3
"""
Jira Helper Script
Direct access to Jira Dominator functionality without MCP protocol.
"""

import sys
import json
from pathlib import Path

# Add current directory to path
current_dir = Path(__file__).parent
sys.path.append(str(current_dir))

from jira_dominator import JiraDominator

def main():
    """Main entry point for the Jira helper."""
    if len(sys.argv) < 2:
        print("Usage: python3 jira_helper.py <command> [arguments...]")
        print("Commands:")
        print("  get_issue_details <issue_key>")
        print("  search_issues <jql> [max_results]")
        print("  get_priority_issues [projects] [priority]")
        print("  test_connection")
        sys.exit(1)
    
    command = sys.argv[1]
    
    try:
        dominator = JiraDominator()
        
        if command == "get_issue_details":
            if len(sys.argv) < 3:
                print("Usage: get_issue_details <issue_key>")
                sys.exit(1)
            
            issue_key = sys.argv[2]
            result = dominator.get_issue_details(issue_key)
            
            if result:
                print(json.dumps(result, indent=2))
            else:
                print(json.dumps({"error": "Issue not found or skipped"}))
        
        elif command == "search_issues":
            if len(sys.argv) < 3:
                print("Usage: search_issues <jql> [max_results]")
                sys.exit(1)
            
            jql = sys.argv[2]
            max_results = int(sys.argv[3]) if len(sys.argv) > 3 else 20
            result = dominator.search_issues_direct(jql, max_results=max_results)
            print(json.dumps(result, indent=2))
        
        elif command == "get_priority_issues":
            projects = sys.argv[2].split(',') if len(sys.argv) > 2 else ["IS", "VRD"]
            priority = sys.argv[3] if len(sys.argv) > 3 else "Highest"
            
            result = {}
            for project in projects:
                try:
                    jql = f'project = "{project}" AND priority = "{priority}" ORDER BY updated DESC'
                    search_result = dominator.search_issues_direct(jql, max_results=10)
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
                    result[project] = f"Error: {str(e)}"
            
            print(json.dumps(result, indent=2))
        
        elif command == "test_connection":
            user = dominator.jira.current_user()
            result = {"status": "connected", "user": user}
            print(json.dumps(result, indent=2))
        
        else:
            print(f"Unknown command: {command}")
            sys.exit(1)
    
    except Exception as e:
        error_result = {"error": str(e)}
        print(json.dumps(error_result, indent=2))
        sys.exit(1)

if __name__ == "__main__":
    main()
