#!/usr/bin/env python3
"""
Build script for Jira Dominator macOS Application

Creates a proper macOS app bundle with all necessary files and dependencies.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

def create_app_bundle():
    """Create the macOS app bundle structure."""
    current_dir = Path(__file__).parent
    app_name = "Jira Dominator"
    app_bundle = current_dir / f"{app_name}.app"
    
    # Remove existing bundle
    if app_bundle.exists():
        shutil.rmtree(app_bundle)
    
    # Create app bundle structure
    contents_dir = app_bundle / "Contents"
    macos_dir = contents_dir / "MacOS"
    resources_dir = contents_dir / "Resources"
    
    for dir_path in [contents_dir, macos_dir, resources_dir]:
        dir_path.mkdir(parents=True, exist_ok=True)
    
    # Create Info.plist
    info_plist = contents_dir / "Info.plist"
    info_plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>jira_dominator_app</string>
    <key>CFBundleIdentifier</key>
    <string>com.jiradominator.app</string>
    <key>CFBundleName</key>
    <string>{app_name}</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleSignature</key>
    <string>????</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.15</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSUIElement</key>
    <true/>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsArbitraryLoads</key>
        <true/>
    </dict>
</dict>
</plist>""")
    
    # Copy Python files to Resources
    python_files = [
        "jira_dominator.py",
        "mcp_server.py",
        "start_mcp_server.py",
        "ollama_integration.py",
        "discover_and_fetch.py",
        "simple_priority_fetch.py",
        "fetch_priority_issues.py",
        "requirements.txt",
        ".env"
    ]
    
    for file_name in python_files:
        src_file = current_dir / file_name
        if src_file.exists():
            shutil.copy2(src_file, resources_dir / file_name)
    
    # Create the main executable script
    main_script = macos_dir / "jira_dominator_app"
    main_script.write_text(f"""#!/usr/bin/env python3
import sys
import os
from pathlib import Path

# Add Resources directory to Python path
resources_dir = Path(__file__).parent.parent / "Resources"
sys.path.insert(0, str(resources_dir))

# Change to Resources directory
os.chdir(resources_dir)

# Import and run the app
from jira_dominator_app import main

if __name__ == "__main__":
    main()
""")
    
    # Make the script executable
    os.chmod(main_script, 0o755)
    
    # Create a launcher script for easy installation
    launcher_script = current_dir / "install_jira_dominator.sh"
    launcher_script.write_text(f"""#!/bin/bash

# Jira Dominator macOS App Installer

echo "🚀 Installing Jira Dominator macOS App..."

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

# Install Python dependencies
echo "📦 Installing Python dependencies..."
pip3 install -r requirements.txt

# Check if Ollama is installed
if ! command -v ollama &> /dev/null; then
    echo "⚠️  Ollama is not installed."
    echo "Please install Ollama from https://ollama.ai"
    echo "After installation, run: ollama pull llama3.2:latest"
fi

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "📝 Creating .env file template..."
    cat > .env << EOF
JIRA_URL=https://your-domain.atlassian.net
JIRA_EMAIL=your-email@domain.com
JIRA_API_TOKEN=your_api_token
EOF
    echo "⚠️  Please edit .env file with your Jira credentials"
fi

echo "✅ Installation complete!"
echo ""
echo "🎯 To use Jira Dominator:"
echo "1. Edit .env file with your Jira credentials"
echo "2. Double-click 'Jira Dominator.app' to start"
echo "3. Look for the 🔧 icon in your menu bar"
echo "4. Click the icon to access controls"
echo ""
echo "🤖 For AI assistant features:"
echo "1. Install Ollama: https://ollama.ai"
echo "2. Run: ollama pull llama3.2:latest"
echo "3. Start Ollama: ollama serve"
echo "4. Use the AI assistant from the menu bar"
""")
    
    os.chmod(launcher_script, 0o755)
    
    print(f"✅ Created macOS app bundle: {app_bundle}")
    print(f"📝 Created installer script: {launcher_script}")
    print()
    print("🎯 Next steps:")
    print("1. Run: ./install_jira_dominator.sh")
    print("2. Edit .env file with your Jira credentials")
    print("3. Double-click 'Jira Dominator.app' to start")
    print("4. Look for the 🔧 icon in your menu bar")

def create_dmg():
    """Create a DMG file for distribution."""
    current_dir = Path(__file__).parent
    app_name = "Jira Dominator"
    app_bundle = current_dir / f"{app_name}.app"
    
    if not app_bundle.exists():
        print("❌ App bundle not found. Run create_app_bundle() first.")
        return
    
    dmg_name = f"{app_name}.dmg"
    dmg_path = current_dir / dmg_name
    
    # Remove existing DMG
    if dmg_path.exists():
        dmg_path.unlink()
    
    try:
        # Create DMG using hdiutil
        subprocess.run([
            "hdiutil", "create",
            "-volname", app_name,
            "-srcfolder", str(app_bundle),
            "-ov", "-format", "UDZO",
            str(dmg_path)
        ], check=True)
        
        print(f"✅ Created DMG: {dmg_path}")
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to create DMG: {e}")
    except FileNotFoundError:
        print("❌ hdiutil not found. DMG creation requires macOS.")

def main():
    """Main build function."""
    print("🔨 Building Jira Dominator macOS Application...")
    print()
    
    create_app_bundle()
    
    # Ask if user wants to create DMG
    if sys.platform == "darwin":
        response = input("\n📦 Create DMG for distribution? (y/n): ").strip().lower()
        if response in ['y', 'yes']:
            create_dmg()

if __name__ == "__main__":
    main()
