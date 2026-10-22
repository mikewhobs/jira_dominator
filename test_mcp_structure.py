#!/usr/bin/env python3
"""
Test script for MCP server structure (doesn't require Jira connection)
"""

import asyncio
import sys
import os

# Add current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import without initializing connection
from jira_mcp_server import list_tools

async def test_structure():
    """Test the MCP server structure."""
    print("=== Testing MCP Server Structure ===\n")
    
    try:
        # Test list_tools
        print("1. Testing tool definitions...")
        tools = await list_tools()
        print(f"✓ Found {len(tools)} tools:")
        
        expected_tools = {
            "jira_test_connection",
            "jira_get_issue_details",
            "jira_get_issue_comments",
            "jira_search_issues",
            "jira_get_related_issues",
            "jira_generate_report",
            "jira_create_issues",
            "jira_update_issues",
            "jira_log_work",
            "jira_link_issues",
            "jira_transition_issue",
            "jira_get_priority_issues",
            "jira_migrate_issues",
            "jira_get_fields",
            "jira_get_createmeta",
            "jira_validate_migration",
        }
        
        actual_tools = {tool.name for tool in tools}
        
        for tool in tools:
            print(f"   - {tool.name}: {tool.description}")
            
        print()
        
        # Check that all expected tools are present
        missing_tools = expected_tools - actual_tools
        extra_tools = actual_tools - expected_tools
        
        if missing_tools:
            print(f"✗ Missing expected tools: {missing_tools}")
            return False
            
        if extra_tools:
            print(f"⚠ Found unexpected tools: {extra_tools}")
            
        print("✓ All expected tools are defined")
        print()
        
        # Test tool schemas
        print("2. Testing tool schemas...")
        for tool in tools:
            schema = tool.inputSchema
            if not isinstance(schema, dict):
                print(f"✗ Tool {tool.name} has invalid schema type")
                return False
                
            if "type" not in schema or schema["type"] != "object":
                print(f"✗ Tool {tool.name} schema is not an object")
                return False
                
        print("✓ All tool schemas are valid")
        print()
        
        print("=== MCP Server Structure Tests Passed! ===")
        print("The server is properly configured and ready to use.")
        print("Connection to Jira will be tested when tools are actually called.")
        return True
        
    except Exception as e:
        print(f"✗ Structure test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_structure())
    sys.exit(0 if success else 1)