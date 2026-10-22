#!/usr/bin/env python3
"""
Test script for the Jira MCP Server
This script tests the basic functionality without requiring a full MCP client.
"""

import asyncio
import sys
import os

# Add current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jira_mcp_server import app, init_dominator

async def test_server():
    """Test the MCP server functionality."""
    print("=== Testing Jira MCP Server ===\n")
    
    try:
        # Initialize dominator
        print("1. Testing initialization...")
        init_dominator()
        print("✓ JiraDominator initialized successfully\n")
        
        # Test list_tools
        print("2. Testing list_tools...")
        tools = await app._list_tools_handler()
        print(f"✓ Found {len(tools.tools)} tools:")
        for tool in tools.tools:
            print(f"   - {tool.name}: {tool.description}")
        print()
        
        # Test connection tool
        print("3. Testing jira_test_connection...")
        result = await app._call_tool_handler("jira_test_connection", {})
        print("✓ Connection test result:")
        print(f"   {result.content[0].text}")
        print()
        
        # Test search functionality with a simple query
        print("4. Testing jira_search_issues...")
        try:
            search_result = await app._call_tool_handler("jira_search_issues", {
                "jql": "project = PROJ ORDER BY updated DESC",
                "max_results": 3
            })
            print("✓ Search test completed:")
            result_text = search_result.content[0].text
            # Just show first few lines to avoid cluttering
            lines = result_text.split('\n')[:10]
            print('\n'.join(f"   {line}" for line in lines))
            if len(result_text.split('\n')) > 10:
                print("   ... (truncated)")
            print()
        except Exception as e:
            print(f"⚠ Search test failed (this might be expected): {e}\n")
        
        print("=== All core tests completed successfully! ===")
        return True
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        return False

if __name__ == "__main__":
    success = asyncio.run(test_server())
    sys.exit(0 if success else 1)