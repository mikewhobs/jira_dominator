#!/usr/bin/env python3
"""
Jira Dominator macOS Application

A native macOS application that runs the Jira Dominator MCP server
with a menu bar interface and Ollama integration.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import psutil
import requests
from AppKit import (
    NSApplication, NSApp, NSMenu, NSMenuItem, NSStatusBar, NSStatusItem,
    NSVariableStatusItemLength, NSImage, NSAlert, NSInformationalAlertStyle,
    NSWorkspace, NSURL, NSApplicationActivationPolicyRegular,
    NSApplicationActivationPolicyAccessory
)
from Foundation import NSObject, NSRunLoop, NSDefaultRunLoopMode
from PyObjCTools import AppHelper
import objc

# Add current directory to path
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))

from mcp_server import JiraDominatorMCPServer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(current_dir / 'jira_dominator.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class JiraDominatorApp(NSObject):
    """Main application class for Jira Dominator macOS app."""
    
    def __init__(self):
        objc.super(JiraDominatorApp, self).__init__()
        self.status_item = None
        self.mcp_server = None
        self.mcp_process = None
        self.ollama_process = None
        self.server_running = False
        self.ollama_running = False
        self.setup_menu_bar()
        self.check_ollama_status()
    
    def setup_menu_bar(self):
        """Set up the menu bar interface."""
        # Create status bar item
        status_bar = NSStatusBar.systemStatusBar()
        self.status_item = status_bar.statusItemWithLength_(NSVariableStatusItemLength)
        
        # Set icon (using a simple text icon for now)
        self.status_item.setTitle_("🔧")
        self.status_item.setToolTip_("Jira Dominator")
        
        # Make sure the status item is visible
        self.status_item.setHighlightMode_(True)
        
        # Create menu
        menu = NSMenu.alloc().init()
        
        # Server status item
        self.server_status_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "MCP Server: Stopped", self.toggle_server, ""
        )
        menu.addItem_(self.server_status_item)
        
        # Ollama status item
        self.ollama_status_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Ollama: Checking...", self.toggle_ollama, ""
        )
        menu.addItem_(self.ollama_status_item)
        
        # Separator
        menu.addItem_(NSMenuItem.separatorItem())
        
        # Quick actions
        quick_search_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quick Search Issues", self.quick_search, ""
        )
        menu.addItem_(quick_search_item)
        
        priority_issues_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Show Priority Issues", self.show_priority_issues, ""
        )
        menu.addItem_(priority_issues_item)
        
        generate_report_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Generate Report", self.generate_report, ""
        )
        menu.addItem_(generate_report_item)
        
        # Separator
        menu.addItem_(NSMenuItem.separatorItem())
        
        # Settings
        settings_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Settings", self.show_settings, ""
        )
        menu.addItem_(settings_item)
        
        # Logs
        logs_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "View Logs", self.view_logs, ""
        )
        menu.addItem_(logs_item)
        
        # Separator
        menu.addItem_(NSMenuItem.separatorItem())
        
        # Quit
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit Jira Dominator", self.quit_app, "q"
        )
        menu.addItem_(quit_item)
        
        # Set menu
        self.status_item.setMenu_(menu)
        
        # Update status
        self.update_status()
    
    def update_status(self):
        """Update the status display."""
        if self.server_running:
            self.server_status_item.setTitle_("MCP Server: Running ✅")
            self.status_item.setTitle_("🔧")
        else:
            self.server_status_item.setTitle_("MCP Server: Stopped ❌")
            self.status_item.setTitle_("🔧")
        
        if self.ollama_running:
            self.ollama_status_item.setTitle_("Ollama: Running ✅")
        else:
            self.ollama_status_item.setTitle_("Ollama: Stopped ❌")
    
    def check_ollama_status(self):
        """Check if Ollama is running."""
        try:
            response = requests.get("http://localhost:11434/api/tags", timeout=2)
            self.ollama_running = response.status_code == 200
        except:
            self.ollama_running = False
        
        self.update_status()
        
        # Check again in 30 seconds
        threading.Timer(30.0, self.check_ollama_status).start()
    
    def toggle_server(self):
        """Toggle MCP server on/off."""
        if self.server_running:
            self.stop_server()
        else:
            self.start_server()
    
    def start_server(self):
        """Start the MCP server."""
        try:
            if self.mcp_process and self.mcp_process.poll() is None:
                self.stop_server()
            
            # Start MCP server in a subprocess
            self.mcp_process = subprocess.Popen([
                sys.executable, str(current_dir / "start_mcp_server.py")
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            self.server_running = True
            self.update_status()
            
            # Show notification
            self.show_notification("Jira Dominator", "MCP Server started successfully")
            
            logger.info("MCP Server started")
            
        except Exception as e:
            logger.error(f"Failed to start MCP server: {e}")
            self.show_alert("Error", f"Failed to start MCP server: {e}")
    
    def stop_server(self):
        """Stop the MCP server."""
        try:
            if self.mcp_process:
                self.mcp_process.terminate()
                self.mcp_process.wait(timeout=5)
                self.mcp_process = None
            
            self.server_running = False
            self.update_status()
            
            logger.info("MCP Server stopped")
            
        except Exception as e:
            logger.error(f"Failed to stop MCP server: {e}")
            self.show_alert("Error", f"Failed to stop MCP server: {e}")
    
    def toggle_ollama(self):
        """Toggle Ollama on/off."""
        if self.ollama_running:
            self.show_alert("Info", "Ollama is already running. You can stop it manually if needed.")
        else:
            self.start_ollama()
    
    def start_ollama(self):
        """Start Ollama."""
        try:
            # Try to start Ollama
            subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Wait a moment and check if it started
            time.sleep(3)
            self.check_ollama_status()
            
            if self.ollama_running:
                self.show_notification("Jira Dominator", "Ollama started successfully")
            else:
                self.show_alert("Error", "Failed to start Ollama. Please check if it's installed.")
                
        except Exception as e:
            logger.error(f"Failed to start Ollama: {e}")
            self.show_alert("Error", f"Failed to start Ollama: {e}")
    
    def quick_search(self):
        """Open quick search dialog."""
        self.show_alert("Quick Search", "Quick search functionality coming soon!")
    
    def show_priority_issues(self):
        """Show priority issues."""
        try:
            # Run the priority issues script
            result = subprocess.run([
                sys.executable, str(current_dir / "discover_and_fetch.py")
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                self.show_alert("Priority Issues", result.stdout)
            else:
                self.show_alert("Error", f"Failed to fetch priority issues: {result.stderr}")
                
        except Exception as e:
            logger.error(f"Failed to show priority issues: {e}")
            self.show_alert("Error", f"Failed to show priority issues: {e}")
    
    def generate_report(self):
        """Generate a report."""
        self.show_alert("Generate Report", "Report generation functionality coming soon!")
    
    def show_settings(self):
        """Show settings dialog."""
        self.show_alert("Settings", "Settings functionality coming soon!")
    
    def view_logs(self):
        """Open log file."""
        try:
            log_file = current_dir / "jira_dominator.log"
            if log_file.exists():
                NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(log_file)))
            else:
                self.show_alert("Info", "No log file found yet.")
        except Exception as e:
            logger.error(f"Failed to open log file: {e}")
            self.show_alert("Error", f"Failed to open log file: {e}")
    
    def quit_app(self):
        """Quit the application."""
        if self.server_running:
            self.stop_server()
        
        NSApp.terminate_(None)
    
    def show_notification(self, title, message):
        """Show a notification."""
        try:
            # Create a simple alert as notification
            alert = NSAlert.alloc().init()
            alert.setMessageText_(title)
            alert.setInformativeText_(message)
            alert.setAlertStyle_(NSInformationalAlertStyle)
            alert.runModal()
        except Exception as e:
            logger.error(f"Failed to show notification: {e}")
    
    def show_alert(self, title, message):
        """Show an alert dialog."""
        try:
            alert = NSAlert.alloc().init()
            alert.setMessageText_(title)
            alert.setInformativeText_(message)
            alert.setAlertStyle_(NSInformationalAlertStyle)
            alert.runModal()
        except Exception as e:
            logger.error(f"Failed to show alert: {e}")

def main():
    """Main entry point."""
    # Create application
    app = NSApplication.sharedApplication()
    
    # Set activation policy to background (menu bar only)
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    
    # Create our app instance
    jira_app = JiraDominatorApp.alloc().init()
    
    # Start the app
    app.run()

if __name__ == "__main__":
    main()
