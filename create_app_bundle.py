#!/usr/bin/env python3
"""
Create a proper macOS app bundle for Jira Dominator
"""

import os
import shutil
import sys
from pathlib import Path

def create_app_bundle():
    """Create a proper macOS app bundle."""
    print("🏗️ Creating macOS app bundle...")
    
    # App bundle structure
    app_name = "JiraDominator.app"
    app_path = Path(app_name)
    
    # Remove existing bundle if it exists
    if app_path.exists():
        shutil.rmtree(app_path)
        print(f"🗑️ Removed existing {app_name}")
    
    # Create app bundle structure
    contents_path = app_path / "Contents"
    macos_path = contents_path / "MacOS"
    resources_path = contents_path / "Resources"
    
    # Create directories
    contents_path.mkdir(parents=True)
    macos_path.mkdir(parents=True)
    resources_path.mkdir(parents=True)
    
    print("📁 Created app bundle structure")
    
    # Create Info.plist
    info_plist = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>JiraDominator</string>
    <key>CFBundleIdentifier</key>
    <string>com.jiradominator.app</string>
    <key>CFBundleName</key>
    <string>Jira Dominator</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>"""
    
    with open(contents_path / "Info.plist", "w") as f:
        f.write(info_plist)
    
    print("📄 Created Info.plist")
    
    # Create the main executable
    main_script = """#!/usr/bin/env python3
import sys
import os
from pathlib import Path

# Add the app bundle resources to the path
bundle_path = Path(__file__).parent.parent.parent
sys.path.insert(0, str(bundle_path))

# Import and run the app
try:
    from working_menu_app import main
    main()
except Exception as e:
    print(f"Error starting app: {e}")
    import traceback
    traceback.print_exc()
    input("Press Enter to exit...")
"""
    
    executable_path = macos_path / "JiraDominator"
    with open(executable_path, "w") as f:
        f.write(main_script)
    
    # Make executable
    os.chmod(executable_path, 0o755)
    
    print("🔧 Created main executable")
    
    # Copy Python files to Resources
    python_files = [
        "working_menu_app.py",
        "jira_dominator.py", 
        "mcp_server.py",
        "ollama_integration.py",
        ".env"
    ]
    
    for file_name in python_files:
        src = Path(file_name)
        if src.exists():
            dst = resources_path / file_name
            shutil.copy2(src, dst)
            print(f"📋 Copied {file_name}")
    
    print(f"✅ App bundle created: {app_path.absolute()}")
    print(f"🚀 You can now run: open {app_name}")
    
    return app_path

if __name__ == "__main__":
    create_app_bundle()
