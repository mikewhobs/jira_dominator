#!/bin/bash

# Jira Dominator Dependencies Installation Script
# This script handles the proper installation of all dependencies, especially PyObjC

echo "🚀 Installing Jira Dominator Dependencies..."
echo "=============================================="

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is required but not installed."
    echo "Please install Python 3 from https://python.org"
    exit 1
fi

# Check if pip is installed
if ! command -v pip3 &> /dev/null; then
    echo "❌ pip3 is required but not installed."
    echo "Please install pip3"
    exit 1
fi

echo "✅ Python 3 and pip3 are available"

# Upgrade pip first
echo "📦 Upgrading pip..."
pip3 install --upgrade pip

# Install basic dependencies first
echo "📦 Installing Python dependencies..."
pip3 install -r requirements.txt

# Install PyObjC - this is the tricky part
echo "📦 Installing PyObjC frameworks..."
echo "   This may take a few minutes..."

# Method 1: Install PyObjC core first
echo "   Method 1: Installing PyObjC core..."
pip3 install pyobjc-core

# Method 2: Install the main PyObjC package (includes most frameworks)
echo "   Method 2: Installing main PyObjC package..."
pip3 install pyobjc

# Method 3: Install specific frameworks that might be missing
echo "   Method 3: Installing specific frameworks..."
pip3 install pyobjc-framework-Cocoa || echo "   Cocoa framework already included"
pip3 install pyobjc-framework-CoreFoundation || echo "   CoreFoundation framework already included"

# Method 4: Try alternative package names
echo "   Method 4: Trying alternative package names..."
pip3 install pyobjc-framework-ApplicationServices || echo "   ApplicationServices not needed"
pip3 install pyobjc-framework-Quartz || echo "   Quartz not needed"

# Method 5: If still having issues, try the full PyObjC suite
if ! python3 -c "import AppKit" 2>/dev/null; then
    echo "   Method 5: Installing full PyObjC suite..."
    pip3 install --upgrade pyobjc
fi

# Test the installation
echo "🧪 Testing installation..."
python3 -c "
try:
    import jira
    print('✅ jira: OK')
except ImportError as e:
    print(f'❌ jira: {e}')

try:
    import dotenv
    print('✅ python-dotenv: OK')
except ImportError as e:
    print(f'❌ python-dotenv: {e}')

try:
    import mcp
    print('✅ mcp: OK')
except ImportError as e:
    print(f'❌ mcp: {e}')

try:
    import AppKit
    print('✅ AppKit: OK')
except ImportError as e:
    print(f'❌ AppKit: {e}')

try:
    import Cocoa
    print('✅ Cocoa: OK')
except ImportError as e:
    print(f'❌ Cocoa: {e}')

try:
    import requests
    print('✅ requests: OK')
except ImportError as e:
    print(f'❌ requests: {e}')

try:
    import psutil
    print('✅ psutil: OK')
except ImportError as e:
    print(f'❌ psutil: {e}')
"

echo ""
echo "🎉 Installation complete!"
echo ""
echo "📋 Next steps:"
echo "1. Create .env file with your Jira credentials"
echo "2. Test the app: python3 launch_app.py"
echo "3. Build the macOS app: python3 build_macos_app.py"
echo ""
echo "🔧 If you encounter PyObjC issues:"
echo "   - Try: pip3 install --upgrade pyobjc"
echo "   - Or: pip3 install pyobjc-framework-Cocoa --force-reinstall"
echo "   - Or use the alternative requirements: pip3 install -r requirements_alternative.txt"
