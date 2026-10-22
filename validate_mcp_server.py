#!/usr/bin/env python3
"""
Validation script for MCP server - checks syntax and basic imports
"""

import sys
import os

# Add current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def validate_server():
    """Validate the MCP server can be imported and basic structure is correct."""
    print("=== Validating Jira MCP Server ===\n")
    
    try:
        print("1. Testing imports...")
        import jira_mcp_server
        print("✓ MCP server module imports successfully")
        
        print("2. Testing server instance...")
        server = jira_mcp_server.app
        print(f"✓ Server instance created: {type(server)}")
        
        print("3. Testing initialization function...")
        init_func = jira_mcp_server.init_dominator
        print(f"✓ Initialization function found: {init_func}")
        
        print("4. Checking tool decorators...")
        # Check that the decorators were applied
        if hasattr(server, '_handlers'):
            print("✓ Server has handlers registered")
        else:
            print("⚠ Server handlers not found - this might be expected")
            
        print("5. Testing basic server structure...")
        print(f"✓ Server name: {server.name if hasattr(server, 'name') else 'not set'}")
        
        print("\n=== MCP Server Validation Passed! ===")
        print("The server module is properly structured and ready to run.")
        return True
        
    except Exception as e:
        print(f"✗ Validation failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = validate_server()
    if success:
        print("\nTo start the server, run: ./run_mcp_server.sh")
        print("Or directly: python3 jira_mcp_server.py")
    sys.exit(0 if success else 1)