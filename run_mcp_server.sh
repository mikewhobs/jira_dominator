#!/bin/bash

# Jira Dominator MCP Server Launch Script

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Change to the script directory
cd "$SCRIPT_DIR"

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo "Error: .env file not found!"
    echo "Please create a .env file with your Jira credentials:"
    echo "JIRA_URL=https://your-domain.atlassian.net"
    echo "JIRA_EMAIL=your-email@example.com"
    echo "JIRA_API_TOKEN=your-api-token"
    exit 1
fi

# Ensure virtual environment exists and points at a working Python (mise/asdf migration safe)
ensure_venv() {
    if [ -x ".venv/bin/python" ] && .venv/bin/python -c "import sys; sys.exit(0)" 2>/dev/null; then
        return 0
    fi

    echo "Creating virtual environment with mise Python..."
    if command -v mise &>/dev/null; then
        mise install python@3.12.13 2>/dev/null || true
        mise x python@3.12.13 -- python -m venv .venv
    else
        python3 -m venv .venv
    fi
}

ensure_venv
echo "Activating virtual environment..."
source .venv/bin/activate

# Install/update requirements
echo "Installing dependencies..."
pip install -r requirements.txt

# Run the MCP server
echo "Starting Jira Dominator MCP Server..."
python jira_mcp_server.py